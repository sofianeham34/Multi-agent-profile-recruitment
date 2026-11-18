"""Config — toutes les valeurs surchargeables via .env."""

import os

from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
# Le projet parle le format d'API « compatible OpenAI », proposé par la plupart des
# fournisseurs : on change de fournisseur dans le .env, sans toucher au code.
# Chaque préréglage : URL de l'API, variable de la clé, modèle et débit par défaut.
PROVIDERS = {
    # OpenRouter, modèles « :free » : gratuits, sans carte bancaire. Sans crédit acheté,
    # rien ne peut être facturé : au-delà de la limite quotidienne, l'API répond 429.
    # La liste des modèles gratuits évolue : https://openrouter.ai/models?q=free
    # Les modèles de secours prennent le relais si le premier est saturé.
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
                   "model": "nvidia/nemotron-3.5-super-vl:free",
                   "fallbacks": ["inclusionai/ling-3.2-flash:free", "google/gemma-4-31b-it:free"],
                   "rps": 0.3, "max_profils": 10},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY",
                "model": "mistral-small-latest", "rps": 1.0},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "model": "openai/gpt-oss-120b", "rps": 0.5},
    # Modèle local servi par Ollama : aucune clé.
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None,
               "model": "llama3.1", "rps": 0},
    # N'importe quel autre fournisseur compatible : LLM_BASE_URL, LLM_API_KEY et LLM_MODEL.
    "custom": {"base_url": None, "key_env": "LLM_API_KEY", "model": None, "rps": 0},
}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()
if LLM_PROVIDER not in PROVIDERS:
    # La valeur n'est pas affichée : une clé collée par erreur sur cette ligne
    # finirait sinon dans les journaux.
    raise RuntimeError(
        f"LLM_PROVIDER contient une valeur inconnue. Valeurs possibles : {', '.join(PROVIDERS)}. "
        "La clé d'API va sur sa propre ligne (par exemple OPENROUTER_API_KEY=...)."
    )
_preset = PROVIDERS[LLM_PROVIDER]
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or _preset["base_url"]
LLM_MODEL = os.getenv("LLM_MODEL") or _preset["model"]
# Modèles de secours (OpenRouter) : LLM_FALLBACK_MODELS=modele1,modele2 ; vide pour désactiver.
if "LLM_FALLBACK_MODELS" in os.environ:
    LLM_FALLBACK_MODELS = [m.strip() for m in os.environ["LLM_FALLBACK_MODELS"].split(",") if m.strip()]
elif "LLM_MODEL" in os.environ:
    LLM_FALLBACK_MODELS = []  # modèle choisi explicitement : pas de bascule implicite
else:
    LLM_FALLBACK_MODELS = list(_preset.get("fallbacks", []))
LLM_API_KEY_ENV = _preset["key_env"]
LLM_API_KEY = os.getenv(LLM_API_KEY_ENV, "") if LLM_API_KEY_ENV else ""

# Débit maximal côté client (requêtes par seconde, 0 = illimité) : on reste sous la
# limite du fournisseur au lieu d'enchaîner les refus.
LLM_REQUESTS_PER_SECOND = float(os.getenv("LLM_REQUESTS_PER_SECOND", _preset["rps"]))

# Effort de raisonnement des modèles gpt-oss (low / medium / high) :
# "low" économise des tokens, important avec la limite de tokens par minute.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")

# Nombre maximal d'appels LLM simultanés (évaluateurs / vérificateurs en parallèle).
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "3"))


def check_llm_config() -> None:
    """Vérifie que le fournisseur choisi est utilisable, avec un message clair sinon."""
    if not LLM_BASE_URL or not LLM_MODEL:
        raise RuntimeError(
            "LLM_PROVIDER=custom demande LLM_BASE_URL et LLM_MODEL dans le fichier .env."
        )
    if LLM_API_KEY_ENV and not LLM_API_KEY:
        raise RuntimeError(
            f"{LLM_API_KEY_ENV} est requis pour le fournisseur {LLM_PROVIDER!r}. "
            "Copiez .env.example vers .env puis renseignez la clé."
        )


def get_llm(temperature: float = 0):
    """Retourne un LLM avec débit limité et relance automatique (erreurs 429, réseau…)."""
    check_llm_config()
    from langchain_core.rate_limiters import InMemoryRateLimiter
    from langchain_openai import ChatOpenAI

    kwargs = {}
    if LLM_REQUESTS_PER_SECOND > 0:
        kwargs["rate_limiter"] = _rate_limiter(InMemoryRateLimiter)
    if "gpt-oss" in LLM_MODEL:
        kwargs["reasoning_effort"] = LLM_REASONING_EFFORT
    if LLM_PROVIDER == "openrouter" and LLM_FALLBACK_MODELS:
        # Routage OpenRouter : essaie les modèles dans l'ordre si le premier est indisponible.
        kwargs["extra_body"] = {"models": [LLM_MODEL, *LLM_FALLBACK_MODELS]}
    llm = ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY or "sans-cle",
        temperature=temperature,
        max_retries=0,  # les relances sont gérées par with_retry ci-dessous
        **kwargs,
    )
    # Seules les erreurs passagères sont relancées : limite de débit (429), réseau,
    # serveur. Une clé invalide ou un modèle inconnu échouent tout de suite.
    import openai

    passageres = (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)
    return llm.with_retry(retry_if_exception_type=passageres, stop_after_attempt=6, wait_exponential_jitter=True)


def describe_llm_error(exc: Exception) -> str:
    """Message lisible pour l'interface quand le fournisseur LLM refuse une requête."""
    import openai

    if isinstance(exc, openai.RateLimitError) and "per-day" in str(exc):
        return (
            f"Quota gratuit du jour atteint chez {LLM_PROVIDER} : la requête a été refusée, "
            "rien n'a été facturé. Le quota se réinitialise chaque jour : réessayez demain."
        )
    if isinstance(exc, openai.RateLimitError):
        return (
            f"Limite du fournisseur LLM ({LLM_PROVIDER}) atteinte : la requête a été refusée, "
            "rien n'a été facturé. Réessayez plus tard ou baissez LLM_REQUESTS_PER_SECOND."
        )
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return f"Clé refusée par le fournisseur LLM ({LLM_PROVIDER}) : vérifiez {LLM_API_KEY_ENV} dans le .env."
    if isinstance(exc, openai.NotFoundError):
        return f"Modèle {LLM_MODEL!r} introuvable chez {LLM_PROVIDER} : vérifiez LLM_MODEL dans le .env."
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return f"Fournisseur LLM injoignable ({LLM_BASE_URL}) : vérifiez la connexion."
    return str(exc)


_limiter = None


def _rate_limiter(factory):
    """Un seul limiteur partagé par tous les agents : le débit vaut pour tout le processus."""
    global _limiter
    if _limiter is None:
        _limiter = factory(
            requests_per_second=LLM_REQUESTS_PER_SECOND,
            check_every_n_seconds=0.1,
            max_bucket_size=1,
        )
    return _limiter


def graph_config(thread_id: str) -> dict:
    """Configuration d'exécution du graphe (fil de discussion + parallélisme borné)."""
    return {"configurable": {"thread_id": thread_id}, "max_concurrency": LLM_MAX_CONCURRENCY}

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
# Avec une offre gratuite limitée en requêtes par jour, on évalue moins de candidats.
MAX_PROFILS_PARALLELES = int(os.getenv("MAX_PROFILS_PARALLELES", _preset.get("max_profils", 10)))

# --- Démo publique (hébergement en ligne) ---
# Protège le quota gratuit et les données quand l'application est ouverte à tous :
# une recherche à la fois, un plafond par jour, suppression désactivée.
DEMO_PUBLIQUE       = os.getenv("DEMO_PUBLIQUE", "0").lower() in ("1", "true", "oui", "yes")
DEMO_MAX_RUNS_JOUR  = int(os.getenv("DEMO_MAX_RUNS_JOUR", "3"))
DEMO_MAX_FICHE      = int(os.getenv("DEMO_MAX_FICHE", "1200"))
HITL_TIMEOUT        = int(os.getenv("HITL_TIMEOUT", "600"))  # secondes avant abandon de la validation
