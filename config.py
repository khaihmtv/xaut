import os
from pathlib import Path

# ─── Đọc file .env thủ công (không cần thư viện python-dotenv) ───
def _load_env(env_path=".env"):
    path = Path(env_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file .env tại: {path.resolve()}\n"
            "Tạo file .env với nội dung:\n"
            "  OKX_API_KEY=your_key\n"
            "  OKX_API_SECRET=your_secret\n"
            "  OKX_PASSPHRASE=your_passphrase\n"
            "  OKX_SIMULATED=1"
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
            f"   Mở file .env và thay 'your_...' bằng giá trị thật."
        )
    return val

API_KEY        = _require("X_API_KEY")
API_SECRET     = _require("X_API_SECRET")
API_PASSPHRASE = _require("X_PASSPHRASE")
SIMULATED      = os.environ.get("OKX_SIMULATED", "1")

# ─── Endpoint ────────────────────────────────────────────────
BASE_URL = "https://www.okx.com"