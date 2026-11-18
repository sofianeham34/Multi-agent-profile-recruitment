"""Choix du fournisseur LLM à partir des variables d'environnement."""

import importlib
from unittest import mock

import pytest

import src.config


def charger(env: dict):
    """Recharge src.config avec un environnement donné (sans lire le .env local)."""
    with mock.patch.dict("os.environ", env, clear=True), mock.patch("dotenv.load_dotenv"):
        return importlib.reload(src.config)


@pytest.fixture(autouse=True)
def restaurer_config():
    yield
    importlib.reload(src.config)


def test_openrouter_par_defaut():
    cfg = charger({})
    assert cfg.LLM_PROVIDER == "openrouter"
    assert cfg.LLM_BASE_URL == "https://openrouter.ai/api/v1"
    assert cfg.LLM_MODEL.endswith(":free")
    assert cfg.LLM_API_KEY_ENV == "OPENROUTER_API_KEY"
    assert cfg.MAX_PROFILS_PARALLELES == 10


def test_limite_de_profils_surchargeable():
    cfg = charger({"MAX_PROFILS_PARALLELES": "8"})
    assert cfg.MAX_PROFILS_PARALLELES == 8
    assert charger({"LLM_PROVIDER": "groq"}).MAX_PROFILS_PARALLELES == 10


def test_preset_mistral():
    cfg = charger({"LLM_PROVIDER": "mistral"})
    assert cfg.LLM_BASE_URL == "https://api.mistral.ai/v1"
    assert cfg.LLM_MODEL == "mistral-small-latest"


def test_cle_manquante_message_clair():
    cfg = charger({})
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        cfg.check_llm_config()


def test_cle_presente():
    cfg = charger({"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "test"})
    cfg.check_llm_config()


def test_ollama_sans_cle():
    cfg = charger({"LLM_PROVIDER": "ollama"})
    cfg.check_llm_config()
    assert cfg.LLM_BASE_URL.startswith("http://localhost:11434")


def test_custom_exige_url_et_modele():
    cfg = charger({"LLM_PROVIDER": "custom", "LLM_API_KEY": "test"})
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        cfg.check_llm_config()
    cfg = charger({"LLM_PROVIDER": "custom", "LLM_API_KEY": "test",
                   "LLM_BASE_URL": "https://exemple.org/v1", "LLM_MODEL": "modele"})
    cfg.check_llm_config()


def test_surcharge_du_modele():
    cfg = charger({"LLM_PROVIDER": "groq", "LLM_MODEL": "openai/gpt-oss-20b"})
    assert cfg.LLM_MODEL == "openai/gpt-oss-20b"
    assert cfg.LLM_API_KEY_ENV == "GROQ_API_KEY"


def test_fournisseur_inconnu_sans_afficher_la_valeur():
    # Une clé collée par erreur à la place du fournisseur ne doit pas apparaître dans l'erreur.
    with pytest.raises(RuntimeError, match="valeur inconnue") as erreur:
        charger({"LLM_PROVIDER": "sk-or-v1-secret"})
    assert "secret" not in str(erreur.value)


def test_message_limite_atteinte():
    import httpx
    import openai

    cfg = charger({"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "test"})
    reponse = httpx.Response(429, request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"))
    message = cfg.describe_llm_error(openai.RateLimitError("trop de requêtes", response=reponse, body=None))
    assert "Limite" in message and "rien n'a été facturé" in message


def test_modeles_de_secours():
    cfg = charger({})
    assert cfg.LLM_FALLBACK_MODELS and all(m.endswith(":free") for m in cfg.LLM_FALLBACK_MODELS)
    assert charger({"LLM_MODEL": "x/y:free"}).LLM_FALLBACK_MODELS == []
    assert charger({"LLM_FALLBACK_MODELS": "a:free, b:free"}).LLM_FALLBACK_MODELS == ["a:free", "b:free"]


def test_message_quota_du_jour():
    import httpx
    import openai

    cfg = charger({"OPENROUTER_API_KEY": "test"})
    reponse = httpx.Response(429, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    exc = openai.RateLimitError("Rate limit exceeded: free-models-per-day.", response=reponse, body=None)
    message = cfg.describe_llm_error(exc)
    assert "Quota gratuit du jour" in message and "demain" in message
