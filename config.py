import os

API_KEY = os.getenv("TOKX_API_KEY")
API_SECRET = os.getenv("TOKX_API_SECRET")
API_PASSPHRASE = os.getenv("TOKX_API_PASSPHRASE")
# ─── Endpoints ──────────────────────────────────────────────────────────────
BASE_URL = "https://www.okx.com"

# ─── Trading Settings ───────────────────────────────────────────────────────
SYMBOL    = "XAU-USDT-SWAP"
TIMEFRAME = "15m"

# ─── Mode ───────────────────────────────────────────────────────────────────
# "1" = Simulated (demo) | "0" = Live (thật) — CẨNTHẬN khi đổi sang "0"
SIMULATED = os.getenv("OKX_SIMULATED", "1")