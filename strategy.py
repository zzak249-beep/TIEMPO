"""
IPDA + edges diferenciales (poco usados en bots retail):

1) Accept/Reject post-sweep (IPDA base)
2) Cross-sectional RVOL rank — solo señales en el percentil alto
   del UNIVERSO en ese ciclo (no un umbral fijo absoluto)
3) BTC-lead regime — alts alineados con el último Accept de BTC
4) Funding-window score — boost/penalización cerca del settlement
5) Absorción multi-métrica — effort (RVOL) vs result (body/ATR)

Nadie combina bien (2)+(3)+(4) en un scanner de perps; la mayoría
usa RVOL absoluto o funding solo como carry, no como filtro de sweep.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

import config


@dataclass
class Signal:
    symbol: str
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    score: int
    src: str
    rvol: float
    atr: float
    dist_atr: float
    days_hint: float
    reason: str
    # edges extra
    xsec_pct: float = 0.0          # percentil RVOL en el ciclo
    funding_min: float = 999.0     # minutos al próximo funding
    absorb: bool = False
    btc_aligned: bool = True


@dataclass
class BtcRegime:
    """Estado del líder (BTC) para filtrar alts."""
    side: Optional[str] = None     # LONG / SHORT / None
    src: Optional[str] = None
    score: int = 0
    ts: float = 0.0


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    prev = np.roll(c, 1)
    prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    out = np.full_like(tr, np.nan, dtype=float)
    if len(tr) < n:
        return out
    out[n - 1] = np.mean(tr[:n])
    for i in range(n, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def _rvol(v: np.ndarray, n: int) -> np.ndarray:
    out = np.ones_like(v, dtype=float)
    for i in range(n, len(v)):
        m = np.mean(v[i - n : i])
        out[i] = v[i] / m if m > 0 else 1.0
    return out


def minutes_to_funding(now_ts: float | None = None) -> float:
    """
    Settlement estándar 00:00 / 08:00 / 16:00 UTC (8h).
    Devuelve minutos hasta el próximo. Edge: sweeps en la ventana
    pre-funding (≤45 min) tienen más probabilidad de grab cuando el
    lado crowded paga funding.
    """
    import time
    t = now_ts if now_ts is not None else time.time()
    # segundos desde medianoche UTC
    day = int(t) % 86400
    slots = [0, 8 * 3600, 16 * 3600, 24 * 3600]
    for s in slots:
        if day < s:
            return (s - day) / 60.0
    return (24 * 3600 - day) / 60.0


def funding_edge(side: str, funding_rate_pct: float, mins: float) -> int:
    """
    Boost de score:
    - Long cerca de funding con funding muy positivo (longs crowded) →
      short más atractivo (fade crowded), long penalizado
    - Inverso si funding negativo
    Ventana activa: ≤ 45 min al settlement.
    """
    if mins > 45:
        return 0
    # funding_rate_pct típico 0.01 = 0.01%
    pts = 0
    if funding_rate_pct >= 0.03:  # longs pagan fuerte
        pts = 8 if side == "SHORT" else -6
    elif funding_rate_pct <= -0.03:
        pts = 8 if side == "LONG" else -6
    # más cerca del settlement, más peso
    if mins <= 15:
        pts = int(pts * 1.5)
    return pts


def analyze(
    symbol: str,
    rows: list[dict],
    funding_rate_pct: float = 0.0,
    btc_regime: Optional[BtcRegime] = None,
) -> Optional[Signal]:
    if len(rows) < max(config.LOOKBACK_B, 50) + 5:
        return None

    o = np.array([r["open"] for r in rows], dtype=float)
    h = np.array([r["high"] for r in rows], dtype=float)
    l = np.array([r["low"] for r in rows], dtype=float)
    c = np.array([r["close"] for r in rows], dtype=float)
    v = np.array([r["volume"] for r in rows], dtype=float)

    i = len(c) - 2  # última cerrada
    if i < config.LOOKBACK_B + config.ACC_BARS + 2:
        return None

    atr = _atr(h, l, c, config.ATR_LEN)
    rvol = _rvol(v, config.VOL_LEN)
    a = atr[i]
    if not np.isfinite(a) or a <= 0:
        return None

    def box_up(n: int) -> float:
        return float(np.max(h[i - n : i]))

    def box_dn(n: int) -> float:
        return float(np.min(l[i - n : i]))

    up_a, dn_a = box_up(config.LOOKBACK_A), box_dn(config.LOOKBACK_A)
    up_b, dn_b = box_up(config.LOOKBACK_B), box_dn(config.LOOKBACK_B)
    cand_up = [x for x in (up_a, up_b) if x > c[i]]
    cand_dn = [x for x in (dn_a, dn_b) if x < c[i]]
    p_up = min(cand_up) if cand_up else up_a
    p_dn = max(cand_dn) if cand_dn else dn_a

    acc = config.ACC_BARS
    win = config.SWEEP_WIN
    sweep_end = i - acc
    if sweep_end < win + 5:
        return None

    sw_up = False
    sw_dn = False
    dep_up = 0.0
    dep_dn = 0.0
    rvol_sw = 1.0
    body_sw = 1.0
    abs_bar = False

    for j in range(sweep_end - win + 1, sweep_end + 1):
        body = abs(c[j] - o[j]) / a if a > 0 else 1.0
        if h[j] > p_up and c[j] <= p_up:
            d = (h[j] - p_up) / a
            if config.MIN_DEP_ATR <= d <= config.MAX_DEP_ATR:
                sw_up = True
                dep_up = max(dep_up, d)
                rvol_sw = max(rvol_sw, float(rvol[j]))
                body_sw = min(body_sw, body)
                # absorción: mucho volumen, poco cuerpo, mecha larga
                if float(rvol[j]) >= config.ABSORB_RVOL and body <= config.ABSORB_BODY:
                    abs_bar = True
        if l[j] < p_dn and c[j] >= p_dn:
            d = (p_dn - l[j]) / a
            if config.MIN_DEP_ATR <= d <= config.MAX_DEP_ATR:
                sw_dn = True
                dep_dn = max(dep_dn, d)
                rvol_sw = max(rvol_sw, float(rvol[j]))
                body_sw = min(body_sw, body)
                if float(rvol[j]) >= config.ABSORB_RVOL and body <= config.ABSORB_BODY:
                    abs_bar = True

    if rvol_sw < config.SWEEP_RVOL:
        return None
    if not sw_up and not sw_dn:
        return None

    side = None
    src = None
    if sw_up:
        closes_above = sum(1 for k in range(sweep_end + 1, i + 1) if c[k] > p_up)
        closes_below = sum(1 for k in range(sweep_end + 1, i + 1) if c[k] < p_up)
        if closes_below >= max(1, acc // 2 + 1):
            side, src = "SHORT", "GRAB"
        elif closes_above >= max(1, acc // 2 + 1) and dep_up >= 0.25:
            side, src = "LONG", "CONT"
    if side is None and sw_dn:
        closes_above = sum(1 for k in range(sweep_end + 1, i + 1) if c[k] > p_dn)
        closes_below = sum(1 for k in range(sweep_end + 1, i + 1) if c[k] < p_dn)
        if closes_above >= max(1, acc // 2 + 1):
            side, src = "LONG", "GRAB"
        elif closes_below >= max(1, acc // 2 + 1) and dep_dn >= 0.25:
            side, src = "SHORT", "CONT"

    if side is None:
        return None
    if side == "LONG" and not config.ALLOW_LONG:
        return None
    if side == "SHORT" and not config.ALLOW_SHORT:
        return None

    # BTC-lead: alts deben alinearse si el filtro está activo
    btc_aligned = True
    if (
        config.USE_BTC_LEAD
        and btc_regime is not None
        and btc_regime.side
        and symbol not in ("BTC-USDT", "BTC-USDT.P")
    ):
        if btc_regime.side != side:
            btc_aligned = False
            if config.BTC_LEAD_STRICT:
                return None

    entry = float(c[i])
    if side == "LONG":
        sl = min(p_dn, float(l[i])) - config.SL_ATR * a
        risk = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + config.TP1_R * risk
        tp2 = entry + config.TP2_R * risk
        dist = abs(entry - p_dn) / a
    else:
        sl = max(p_up, float(h[i])) + config.SL_ATR * a
        risk = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - config.TP1_R * risk
        tp2 = entry - config.TP2_R * risk
        dist = abs(p_up - entry) / a

    mins_f = minutes_to_funding()
    f_pts = funding_edge(side, funding_rate_pct, mins_f) if config.USE_FUNDING_EDGE else 0

    score = 40
    if rvol_sw >= config.SWEEP_RVOL:
        score += 10
    if dist <= config.NEAR_ATR:
        score += 12
    if src == "GRAB":
        score += 10
    if src == "CONT":
        score += 8
    if abs_bar:
        score += 10
    if btc_aligned and btc_regime and btc_regime.side:
        score += 6
    score += f_pts
    if i >= 50:
        ema = float(np.mean(c[i - 49 : i + 1]))
        if side == "LONG" and entry > ema:
            score += 8
        if side == "SHORT" and entry < ema:
            score += 8
    if rvol_sw >= 1.8:
        score += 5
    score = int(max(0, min(100, score)))

    # El corte final por score se aplica DESPUÉS del rank cross-sectional
    # en bot.py; aquí devolvemos candidatos con score mínimo blando
    if score < config.MIN_SCORE - 15:
        return None

    bars_day = 96.0 if "15" in config.TIMEFRAME else 288.0 if "5" in config.TIMEFRAME else 24.0
    days_hint = acc / bars_day

    reason = (
        f"{src} {'ABS ' if abs_bar else ''}RVOL {rvol_sw:.2f}x · "
        f"dist {dist:.2f}ATR · fund {mins_f:.0f}m · "
        f"{'BTC✓' if btc_aligned else 'BTC✗'}"
    )
    return Signal(
        symbol=symbol,
        side=side,
        entry=entry,
        sl=float(sl),
        tp1=float(tp1),
        tp2=float(tp2),
        score=score,
        src=src or "—",
        rvol=float(rvol_sw),
        atr=float(a),
        dist_atr=float(dist),
        days_hint=float(days_hint),
        reason=reason,
        funding_min=float(mins_f),
        absorb=abs_bar,
        btc_aligned=btc_aligned,
    )


def apply_cross_sectional(
    candidates: list[Signal],
    min_pct: float | None = None,
) -> list[Signal]:
    """
    EDGE CLAVE: solo deja pasar señales cuyo RVOL está en el percentil
    alto del CICLO actual (entre todos los candidatos del scan).

    Umbral absoluto de RVOL engaña: en días muertos 1.3x es ruido; en
    días vivos 1.3x es mediocre. El rank relativo corrige eso.
    """
    if not candidates:
        return []
    pct_need = min_pct if min_pct is not None else config.XSEC_RVOL_PCT
    if len(candidates) == 1:
        candidates[0].xsec_pct = 100.0
        return candidates if candidates[0].score >= config.MIN_SCORE else []

    rvols = sorted(s.rvol for s in candidates)
    out: list[Signal] = []
    for s in candidates:
        # percentil empírico
        rank = sum(1 for x in rvols if x <= s.rvol) / len(rvols) * 100.0
        s.xsec_pct = rank
        # boost score por estar en la cola derecha
        if rank >= pct_need:
            s.score = int(min(100, s.score + int((rank - pct_need) / 5)))
            if s.score >= config.MIN_SCORE:
                out.append(s)
    # ordenar por score desc
    out.sort(key=lambda x: (x.score, x.xsec_pct), reverse=True)
    return out
