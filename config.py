import os

# ─── OKX Credentials ────────────────────────────────────────────────────────
# Khuyến nghị: dùng biến môi trường thay vì hardcode
API_KEY        = os.getenv("OKX_API_KEY", "ec52d21f-417e-498a-8385-0bf0f7a21f4a")
API_SECRET     = os.getenv("OKX_API_SECRET", "D712A4DD310157CDFB035F55F305A9C2")
API_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "lMmkll!1")
# ─── Endpoints ──────────────────────────────────────────────────────────────
BASE_URL = "https://www.okx.com"

# ─── Trading Settings ───────────────────────────────────────────────────────
SYMBOL    = "BTC-USDT-SWAP"
TIMEFRAME = "15m"

# ─── Mode ───────────────────────────────────────────────────────────────────
# "1" = Simulated (demo) | "0" = Live (thật) — CẨNTHẬN khi đổi sang "0"
SIMULATED = os.getenv("OKX_SIMULATED", "1")