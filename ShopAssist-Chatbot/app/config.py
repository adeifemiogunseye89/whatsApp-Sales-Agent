import os
from pathlib import Path


def _load_dotenv():
    """Read the .env file next to the project (no extra library needed).
    Real environment variables still win if both exist."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ---- WhatsApp ----
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "dev-verify-token")
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0")

# ---- LLM ----
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# ---- Business ----
DATABASE_PATH = os.getenv("DATABASE_PATH", "shop.db")
# Money is handled in KOBO (integer, 100 kobo = 1 naira) everywhere in the code.
# TAX_RATE defaults to 0 because shelf prices are treated as VAT-inclusive.
# Confirm the correct VAT treatment of your products with your accountant.
TAX_RATE = float(os.getenv("TAX_RATE", "0"))
DELIVERY_FEE_KOBO = int(os.getenv("DELIVERY_FEE_KOBO", "0"))   # pickup-only = 0
RESERVATION_HOURS = int(os.getenv("RESERVATION_HOURS", "5"))   # how long we hold stock
QUOTE_TTL_MINUTES = int(os.getenv("QUOTE_TTL_MINUTES", "10"))  # how long a quote can be confirmed
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))  # idle time before a chat is forgotten
HANDOFF_HOURS = int(os.getenv("HANDOFF_HOURS", "4"))           # bot stays silent this long after escalation
HISTORY_CAP = int(os.getenv("HISTORY_CAP", "20"))              # max chat messages kept / sent to the model
HUMAN_ESCALATION_WEBHOOK = os.getenv("HUMAN_ESCALATION_WEBHOOK", "")
CURRENCY = os.getenv("CURRENCY", "₦")
