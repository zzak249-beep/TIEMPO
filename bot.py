"""
IPDA BingX Bot v2 — edges: cross-sectional RVOL, BTC-lead, funding window.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from typing import Optional

import httpx

import config
from bingx import BingX, BingXError
from notify import State, Telegram
from strategy import BtcRegime, Signal, analyze, apply_cross_sectional, minutes_to_funding

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ipda")
STOP = asyncio.Event()


def _handle_sig(*_):
    log.info("Parada")
    STOP.set()


def fmt_sig(s: Signal, mode: str) -> str:
    arrow = "🟢 LONG" if s.side == "LONG" else "🔴 SHORT"
    live = "LIVE" if mode == "LIVE" else "SOLO SEÑAL"
    abs_t = " · ABSORCIÓN" if s.absorb else ""
    return (
        f"<b>IPDA v2 · {arrow}</b> · {live}{abs_t}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Par: <b>{s.symbol}</b>\n"
        f"Origen: {s.src} · Score <b>{s.score}/100</b>\n"
        f"XSec RVOL: <b>p{s.xsec_pct:.0f}</b> · Fund {s.funding_min:.0f}m\n"
        f"BTC: {'alineado ✓' if s.btc_aligned else 'divergente'}\n"
        f"\n"
        f"📍 Entrada: <code>{s.entry}</code>\n"
        f"🛑 SL: <code>{s.sl}</code>\n"
        f"🎯 TP1: <code>{s.tp1}</code> · TP2: <code>{s.tp2}</code>\n"
        f"\n"
        f"RVOL {s.rvol:.2f}x · Dist {s.dist_atr:.2f} ATR\n"
        f"{s.reason}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"TF {config.TIMEFRAME} · MODE={mode}"
    )


async def pick_symbols(bx: BingX) -> list[str]:
    if config.SYMBOLS:
        syms = list(config.SYMBOLS)
    else:
        try:
            all_syms = await bx.symbols()
            vols = await bx.tickers_24h()
            ranked = sorted(
                (s for s in all_syms if vols.get(s, 0) >= config.MIN_QUOTE_VOL_24H),
                key=lambda s: vols.get(s, 0),
                reverse=True,
            )
            syms = ranked[: config.MAX_SYMBOLS]
        except Exception as exc:
            log.error("simbolos: %s", exc)
            syms = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    btc = config.BTC_SYMBOL
    if config.USE_BTC_LEAD and btc not in syms:
        syms = [btc] + syms
    elif config.USE_BTC_LEAD and btc in syms:
        syms = [btc] + [s for s in syms if s != btc]
    return syms[: config.MAX_SYMBOLS + 1]


async def try_live_order(bx: BingX, sig: Signal, equity: float) -> Optional[str]:
    risk_cash = equity * (config.RISK_PCT / 100.0)
    risk_px = abs(sig.entry - sig.sl)
    if risk_px <= 0 or equity <= 0:
        return None
    qty = risk_cash / risk_px
    qty = min(qty, (equity * config.MAX_LEVERAGE) / sig.entry)
    qty = bx.round_qty(sig.symbol, qty)
    if qty < bx.min_qty(sig.symbol):
        log.warning("%s qty baja %.6f", sig.symbol, qty)
        return None
    side = "BUY" if sig.side == "LONG" else "SELL"
    pos_side = "LONG" if sig.side == "LONG" else "SHORT"
    sl = bx.round_price(sig.symbol, sig.sl)
    tp = bx.round_price(sig.symbol, sig.tp1)
    try:
        data = await bx._private(
            "POST",
            "/openApi/swap/v2/trade/order",
            {
                "symbol": sig.symbol,
                "side": side,
                "positionSide": pos_side,
                "type": "MARKET",
                "quantity": qty,
            },
        )
        oid = str(data.get("orderId") or data.get("orderID") or "ok")
        exit_side = "SELL" if side == "BUY" else "BUY"
        for typ, px in (("STOP_MARKET", sl), ("TAKE_PROFIT_MARKET", tp)):
            await bx._private(
                "POST",
                "/openApi/swap/v2/trade/order",
                {
                    "symbol": sig.symbol,
                    "side": exit_side,
                    "positionSide": pos_side,
                    "type": typ,
                    "stopPrice": px,
                    "quantity": qty,
                    "workingType": "MARK_PRICE",
                },
            )
        return oid
    except BingXError as exc:
        log.error("LIVE %s: %s", sig.symbol, exc)
        return None


async def scan_once(
    bx: BingX,
    tg: Telegram,
    state: State,
    symbols: list[str],
    mode: str,
    funding_map: dict,
) -> BtcRegime:
    state.roll_day()
    equity = 0.0
    if mode == "LIVE":
        try:
            equity = (await bx.cuenta()).get("equity", 0.0) or await bx.balance_usdt()
        except Exception as exc:
            log.warning("balance: %s", exc)

    btc_regime = BtcRegime()
    candidates = []

    for sym in symbols:
        if STOP.is_set():
            break
        try:
            rows = await bx.klines(sym, config.TIMEFRAME, config.KLINE_LIMIT)
        except Exception as exc:
            log.debug("klines %s: %s", sym, exc)
            await asyncio.sleep(0.12)
            continue

        fr = float(funding_map.get(sym, 0.0))
        reg = None if sym == config.BTC_SYMBOL else btc_regime
        sig = analyze(sym, rows, funding_rate_pct=fr, btc_regime=reg)
        if not sig:
            await asyncio.sleep(0.04)
            continue

        if sym == config.BTC_SYMBOL:
            btc_regime = BtcRegime(
                side=sig.side, src=sig.src, score=sig.score, ts=time.time()
            )
            log.info("BTC regimen -> %s %s score=%s", sig.side, sig.src, sig.score)

        if not state.can_trade(sym):
            continue
        last = state.data.get("last_signal", {}).get(sym, 0)
        if time.time() - float(last) < config.COOLDOWN_BARS:
            continue
        candidates.append(sig)
        await asyncio.sleep(0.05)

    if config.USE_XSEC_RVOL:
        final = apply_cross_sectional(candidates)
    else:
        final = [s for s in candidates if s.score >= config.MIN_SCORE]
        final.sort(key=lambda x: x.score, reverse=True)

    final = final[: config.MAX_SIGNALS_CYCLE]

    for sig in final:
        log.info(
            "SENAL %s %s score=%s xsec=p%.0f %s",
            sig.side, sig.symbol, sig.score, sig.xsec_pct, sig.src,
        )
        await tg.send(fmt_sig(sig, mode))
        state.mark_signal(sig.symbol)

        if mode == "LIVE" and config.LIVE_CONFIRMED and equity > 0:
            oid = await try_live_order(bx, sig, equity)
            if oid:
                state.data.setdefault("open", {})[sig.symbol] = {
                    "side": sig.side,
                    "entry": sig.entry,
                    "sl": sig.sl,
                    "tp": sig.tp1,
                    "ts": time.time(),
                    "oid": oid,
                }
                state.save()
                await tg.send(f"OK Orden <b>{sig.symbol}</b> {sig.side}")
            else:
                await tg.send(f"WARN {sig.symbol} senal OK, orden rechazada")

    if candidates and not final:
        log.info(
            "Ciclo: %d candidatos, 0 pasan XSec/score (min p%.0f score>=%s)",
            len(candidates), config.XSEC_RVOL_PCT, config.MIN_SCORE,
        )
    return btc_regime


async def main() -> None:
    mode = config.MODE
    if mode == "LIVE" and not config.LIVE_CONFIRMED:
        log.warning("LIVE sin LIVE_CONFIRMED -> SIGNAL")
        mode = "SIGNAL"
    if mode == "LIVE" and not (config.BINGX_API_KEY and config.BINGX_API_SECRET):
        mode = "SIGNAL"

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    state = State(config.STATE_PATH)
    async with httpx.AsyncClient() as client:
        bx = BingX(client)
        tg = Telegram(client)
        symbols = await pick_symbols(bx)
        mins = minutes_to_funding()
        await tg.send(
            f"IPDA v2 arrancado\n"
            f"Modo: <b>{mode}</b> · TF {config.TIMEFRAME}\n"
            f"Simbolos: {len(symbols)}\n"
            f"Edges: XSec RVOL p>={config.XSEC_RVOL_PCT:.0f} · "
            f"BTC-lead={'ON' if config.USE_BTC_LEAD else 'off'} · "
            f"Funding={'ON' if config.USE_FUNDING_EDGE else 'off'}\n"
            f"Proximo funding en ~{mins:.0f} min\n"
            f"Max {config.MAX_SIGNALS_CYCLE} senales/ciclo · score>={config.MIN_SCORE}"
        )

        cycle = 0
        while not STOP.is_set():
            t0 = time.time()
            funding_map = {}
            try:
                funding_map = await bx.funding_rates()
            except Exception as exc:
                log.debug("funding: %s", exc)
            try:
                await scan_once(bx, tg, state, symbols, mode, funding_map)
            except Exception as exc:
                log.exception("scan: %s", exc)
            cycle += 1
            if cycle % 15 == 0:
                symbols = await pick_symbols(bx)
                await tg.send(
                    f"Heartbeat · {len(symbols)} sym · "
                    f"trades dia {state.data.get('day_trades', 0)} · "
                    f"fund ~{minutes_to_funding():.0f}m"
                )
            wait = max(5.0, config.SCAN_SEC - (time.time() - t0))
            try:
                await asyncio.wait_for(STOP.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
        await tg.send("IPDA v2 detenido")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
