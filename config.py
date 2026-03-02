import os
from pathlib import Path

# ─── Đọc file .env ───────────────────────────────────────────
def _load_env(env_path=".env"):
    path = Path(env_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file .env tại: {path.resolve()}\n"
            "Tạo file .env với nội dung:\n"
            "  OKX_SIMULATED=1\n\n"
            "  # Demo credentials\n"
            "  X_API_KEY=your_demo_key\n"
            "  X_API_SECRET=your_demo_secret\n"
            "  X_PASSPHRASE=your_demo_passphrase\n\n"
            "  # Live credentials\n"
            "  OKX_API_KEY=your_live_key\n"
            "  OKX_API_SECRET=your_live_secret\n"
            "  OKX_PASSPHRASE=your_live_passphrase"
        )
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

_load_env()

# ─── Đọc biến và kiểm tra ────────────────────────────────────
def _require(key: str) -> str:
    val = os.environ.get(key, "")
    if not val or val.startswith("your_"):
        raise ValueError(
            f"❌ Biến '{key}' chưa được điền trong file .env!\n"
            f"   Mở file .env và điền giá trị thật vào."
        )
    return val

# ─── Chọn credentials theo mode ──────────────────────────────
SIMULATED = os.environ.get("OKX_SIMULATED", "1")

if SIMULATED == "0":
    # 🔴 LIVE — dùng API key tài khoản thật
    API_KEY        = _require("OKX_API_KEY")
    API_SECRET     = _require("OKX_API_SECRET")
    API_PASSPHRASE = _require("OKX_PASSPHRASE")
    print("⚠️  [CONFIG] Mode: 🔴 LIVE — Đang dùng tiền THẬT!")
else:
    # 🔵 DEMO — dùng API key tài khoản demo
    API_KEY        = _require("X_API_KEY")
    API_SECRET     = _require("X_API_SECRET")
    API_PASSPHRASE = _require("X_PASSPHRASE")
    print("✅ [CONFIG] Mode: 🔵 SIMULATED — Đang dùng tài khoản demo")

# ─── Endpoint ────────────────────────────────────────────────
BASE_URL = "https://www.okx.com"