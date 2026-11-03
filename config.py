"""
Configuración IPDA BingX Bot v2 — edges diferenciales.
"""
from __future__ import annotations
import os


def _bool(n: str, d: bool = False) -> bool:
    return os.getenv(n, str(d)).strip().lower() in ("1", "true", "yes", "si", "sí")


def _float(n: str, d: float) -> float:
    try:
        return float(os.getenv(n, str(d)))
    except (TypeError, ValueError):
        return d


def _int(n: str, d: int) -> int:
    try:
        return int(os.getenv(n, str(d)))
    except (TypeError, ValueError):
        return d


MODE = os.getenv("MODE", "SIGNAL").strip().upper()
LIVE_CONFIRMED = _bool("LIVE_CONFIRMED", False)

BINGX_API_KEY = os.getenv("BINGX_API_KEY", "").strip()
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "").strip()
BINGX_BASE_URL = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").strip()
RECV_WINDOW = _int("RECV_WINDOW", 5000)
POST_ONLY = _bool("POST_ONLY", True)

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()
TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or ""
).strip()

TIMEFRAME = os.getenv("TIMEFRAME", "15m").strip()
KLINE_LIMIT = _int("KLINE_LIMIT", 120)
SCAN_SEC = _int("SCAN_SEC", 90)
MAX_SYMBOLS = _int("MAX_SYMBOLS", 40)
MIN_QUOTE_VOL_24H = _float("MIN_QUOTE_VOL_24H", 5_000_000)
EXCLUDE_PREFIXES = tuple(
    p.strip().upper()
    for p in os.getenv("EXCLUDE_PREFIXES", "USDC,EUR,BUSD,TUSD").split(",")
    if p.strip()
)
SYMBOLS = [
    s.strip().upper().replace("USDT", "-USDT") if "-" not in s.strip().upper() else s.strip().upper()
    for s in os.getenv("SYMBOLS", "").split(",")
    if s.strip()
]

LOOKBACK_A = _int("LOOKBACK_A", 20)
LOOKBACK_B = _int("LOOKBACK_B", 40)
SWEEP_WIN = _int("SWEEP_WIN", 3)
MIN_DEP_ATR = _float("MIN_DEP_ATR", 0.03)
MAX_DEP_ATR = _float("MAX_DEP_ATR", 0.55)
SWEEP_RVOL = _float("SWEEP_RVOL", 1.20)
ACC_BARS = _int("ACC_BARS", 3)
NEAR_ATR = _float("NEAR_ATR", 0.45)
MIN_SCORE = _int("MIN_SCORE", 55)
ALLOW_LONG = _bool("ALLOW_LONG", True)
ALLOW_SHORT = _bool("ALLOW_SHORT", True)
ATR_LEN = _int("ATR_LEN", 14)
VOL_LEN = _int("VOL_LEN", 20)

# Edges diferenciales
USE_XSEC_RVOL = _bool("USE_XSEC_RVOL", True)
XSEC_RVOL_PCT = _float("XSEC_RVOL_PCT", 70.0)
USE_BTC_LEAD = _bool("USE_BTC_LEAD", True)
BTC_LEAD_STRICT = _bool("BTC_LEAD_STRICT", False)
BTC_SYMBOL = os.getenv("BTC_SYMBOL", "BTC-USDT").strip()
USE_FUNDING_EDGE = _bool("USE_FUNDING_EDGE", True)
ABSORB_RVOL = _float("ABSORB_RVOL", 1.50)
ABSORB_BODY = _float("ABSORB_BODY", 0.35)
MAX_SIGNALS_CYCLE = _int("MAX_SIGNALS_CYCLE", 3)

RISK_PCT = _float("RISK_PCT", 0.85)
MAX_LEVERAGE = _float("MAX_LEVERAGE", 5.0)
SL_ATR = _float("SL_ATR", 0.35)
TP1_R = _float("TP1_R", 1.0)
TP2_R = _float("TP2_R", 2.0)
MAX_TRADES_DAY = _int("MAX_TRADES_DAY", 4)
MAX_LOSSES_DAY = _int("MAX_LOSSES_DAY", 2)
COOLDOWN_BARS = _int("COOLDOWN_SEC", 300)
MAX_OPEN = _int("MAX_OPEN", 3)

STATE_PATH = os.getenv("STATE_PATH", "/data/ipda_state.json")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
