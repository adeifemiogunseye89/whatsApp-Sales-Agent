import os

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
TAX_RATE = float(os.getenv("TAX_RATE", "0.075"))
DELIVERY_FEE = float(os.getenv("DELIVERY_FEE", "2.00"))
HUMAN_ESCALATION_WEBHOOK = os.getenv("HUMAN_ESCALATION_WEBHOOK", "")
CURRENCY = os.getenv("CURRENCY", "$")
