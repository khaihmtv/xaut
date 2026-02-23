import os

SYMBOL = "XAU-USDT-SWAP"
TIMEFRAME = "15m"

TOTAL_CAPITAL = float(os.getenv("TOTAL_CAPITAL", 400))
BOT_RATIO = 0.6
LEVERAGE = 2

GRID_LEVELS = 12
EMA_PERIOD = 200
ATR_PERIOD = 14

KILL_SWITCH_DRAWDOWN = 0.2
MAX_FUNDING = 0.0005

API_KEY = os.getenv("TOKX_API_KEY")
API_SECRET = os.getenv("TOKX_API_SECRET")
API_PASSPHRASE = os.getenv("TOKX_API_PASSPHRASE")


# ─── Mode ───────────────────────────────────────────────────────────────────
# "1" = Simulated (demo) | "0" = Live (thật) — CẨNTHẬN khi đổi sang "0"
SIMULATED = os.getenv("OKX_SIMULATED", "1")
BASE_URL = "https://www.okx.com"