"""Config — toutes les valeurs surchargeables via .env."""

import os

from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
if "LLM_MODEL" in os.environ:
    LLM_MODEL = os.environ["LLM_MODEL"]
elif LLM_PROVIDER == "groq":
    LLM_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
else:
    LLM_MODEL = "mistral"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


def get_llm(temperature: float = 0):
    """Retourne un LLM avec retry Groq."""
    if LLM_PROVIDER == "groq" and not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY est requis pour utiliser Groq. "
            "Copiez .env.example vers .env puis renseignez GROQ_API_KEY."
        )
    from langchain_classic.chat_models import init_chat_model
    llm = init_chat_model(LLM_MODEL, model_provider=LLM_PROVIDER, temperature=temperature)
    return llm.with_retry(stop_after_attempt=6, wait_exponential_jitter=True)

# --- APIs externes ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")        # 60 req/h sans token
STACKOVERFLOW_KEY = os.getenv("STACKOVERFLOW_KEY", "")

# --- Seuils de décision A5 ---
SCORE_SEUIL_CONTACT = int(os.getenv("SCORE_SEUIL_CONTACT", "75"))  # → A7 contacte
SCORE_SEUIL_HUMAIN  = int(os.getenv("SCORE_SEUIL_HUMAIN",  "50"))  # → HITL
SCORE_SEUIL_VIABLE  = int(os.getenv("SCORE_SEUIL_VIABLE",  "40"))  # → mode relatif
TOP_N_RELATIF       = int(os.getenv("TOP_N_RELATIF", "3"))

# --- Limites pipeline ---
MAX_PROFILS_RECHERCHE  = int(os.getenv("MAX_PROFILS_RECHERCHE",  "15"))
MAX_PROFILS_PARALLELES = int(os.getenv("MAX_PROFILS_PARALLELES", "10"))
