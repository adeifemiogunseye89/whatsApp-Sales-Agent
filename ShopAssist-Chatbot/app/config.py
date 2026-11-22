import os
from pathlib import Path


def _load_dotenv(env_path=None):
    """Read the .env file next to the project (no extra library needed).
    Real environment variables still win if both exist.
    Set SHOPASSIST_NO_DOTENV=1 to skip it: the test suites do, so your private .env
    (real keys, your own lanes and prices) can never change a test result."""
    if os.environ.get("SHOPASSIST_NO_DOTENV") == "1":
        return
    env_path = Path(env_path) if env_path else Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _int(name: str, default: str) -> int:
    """A whole-number setting. A typo in .env gives a message that says what to fix."""
    raw = os.getenv(name, default).strip()
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Setting {name} must be a whole number, but .env says '{raw}'. "
                         f"Fix it in your .env file (for example {name}={default}).") from None


def _float(name: str, default: str) -> float:
    raw = os.getenv(name, default).strip()
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"Setting {name} must be a number, but .env says '{raw}'. "
                         f"Fix it in your .env file (for example {name}={default}).") from None


_load_dotenv()

# ---- WhatsApp ----
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "dev-verify-token")
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0")

# ---- LLM ----
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_NUM_CTX = _int("OLLAMA_NUM_CTX", "6144")      # context window for the local model
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "1h")       # keep the local model loaded between chats

# ---- Brain: model lanes (Stage 4.1) ----
# Each lane is "provider:model". Providers: anthropic, openai, groq, gemini,
# openrouter, mistral, cerebras, ollama. Lanes are tried in this order:
# primary -> backup -> local. Leave one empty to switch it off.
LLM_PRIMARY = os.getenv("LLM_PRIMARY", "")                     # e.g. anthropic:claude-haiku-5-5
LLM_BACKUP = os.getenv("LLM_BACKUP", "")                       # e.g. groq:openai/gpt-oss-120b (model names change: run python -m check_lanes --models)
LLM_LOCAL = os.getenv("LLM_LOCAL", f"ollama:{OLLAMA_MODEL}")   # slow last resort; "" = none
LLM_TIMEOUT_HOSTED = _int("LLM_TIMEOUT_HOSTED", "20")    # seconds per hosted call
LLM_TIMEOUT_LOCAL = _int("LLM_TIMEOUT_LOCAL", "300")     # seconds per local call
LLM_MAX_OUTPUT_TOKENS = _int("LLM_MAX_OUTPUT_TOKENS", "300")
DAILY_BUDGET_USD = _float("DAILY_BUDGET_USD", "0.50")    # 0 = no cap
BUDGET_ALERT_PERCENT = _int("BUDGET_ALERT_PERCENT", "80")
MODEL_TURNS_PER_HOUR = _int("MODEL_TURNS_PER_HOUR", "30")    # per phone
MAX_MESSAGE_CHARS = _int("MAX_MESSAGE_CHARS", "600")         # longer messages are cut
LLM_WARM_UP = os.getenv("LLM_WARM_UP", "0") == "1"                  # load the local model at server start
LIMITED_MODE_MESSAGE = os.getenv("LIMITED_MODE_MESSAGE",
                                 "We're a bit busy; the team will reply shortly.")

# ---- Business ----
DATABASE_PATH = os.getenv("DATABASE_PATH", "shop.db")
# Money is handled in KOBO (integer, 100 kobo = 1 naira) everywhere in the code.
# TAX_RATE defaults to 0 because shelf prices are treated as VAT-inclusive.
# Confirm the correct VAT treatment of your products with your accountant.
TAX_RATE = _float("TAX_RATE", "0")
DELIVERY_FEE_KOBO = _int("DELIVERY_FEE_KOBO", "0")   # pickup-only = 0
RESERVATION_HOURS = _int("RESERVATION_HOURS", "2")   # how long we hold stock
QUOTE_TTL_MINUTES = _int("QUOTE_TTL_MINUTES", "10")  # how long a quote can be confirmed
GRACE_MINUTES = _int("GRACE_MINUTES", "30")          # extra time to collect after the hold ends
DAILY_RESERVATION_LIMIT = _int("DAILY_RESERVATION_LIMIT", "3")   # per phone, rolling 24 hours
SWEEP_MINUTES = _int("SWEEP_MINUTES", "5")           # how often expired holds are released
LOG_DIR = os.getenv("LOG_DIR", "logs")                         # per-turn diagnostics (no customer text)
SESSION_TTL_HOURS = _int("SESSION_TTL_HOURS", "24")  # idle time before a chat is forgotten
HANDOFF_HOURS = _int("HANDOFF_HOURS", "4")           # bot stays silent this long after escalation
HISTORY_CAP = _int("HISTORY_CAP", "20")              # max chat messages kept / sent to the model
HUMAN_ESCALATION_WEBHOOK = os.getenv("HUMAN_ESCALATION_WEBHOOK", "")
CURRENCY = os.getenv("CURRENCY", "₦")
