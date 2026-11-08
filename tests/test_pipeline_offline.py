"""Exécution complète du graphe (A1 → A8) sans réseau ni clé API.

Le LLM et les sources (DuckDuckGo, GitHub, Stack Overflow, scraping) sont
remplacés par des doublures déterministes : on vérifie l'orchestration, le
fan-out parallèle, le routage et la gestion d'une réponse LLM inexploitable.
"""

import json
from unittest import mock

import pytest
from langchain_core.messages import AIMessage

FICHE = """Développeur Python backend confirmé (5 ans minimum) à Montpellier.
Compétences : Python, FastAPI, PostgreSQL, Docker. CDI, hybride."""

PROFILS = {
    "https://github.com/alice-dev": (
        "Alice Martin",
        "Alice Martin — Développeuse backend Python à Montpellier. 7 ans d'expérience. "
        "Python, FastAPI, PostgreSQL, Docker, Kubernetes. Open source : 40 dépôts.",
    ),
    "https://github.com/bob-front": (
        "Bob Durand",
        "Bob Durand — Développeur front-end React à Lyon. 2 ans d'expérience. JavaScript, CSS.",
    ),
    "https://github.com/carol-data": (
        "Carol Petit",
        "Carol Petit — Data engineer Python à Montpellier. 6 ans d'expérience. Python, Spark, Docker.",
    ),
}


class FakeLLM:
    """Doublure de LLM : la réponse dépend du contenu du dernier message."""

    def __init__(self, repondre):
        self.repondre = repondre
        self.appels = 0

    def invoke(self, messages):
        self.appels += 1
        texte = messages[-1].content
        return AIMessage(content=self.repondre(texte))


def analyste(_):
    return "```json\n" + json.dumps({
        "hard_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "soft_skills": ["autonomie"],
        "experience_min": 5,
        "formation": "Bac+5",
        "contraintes": [],
        "mots_cles": ["python", "backend", "fastapi"],
        "localisations": ["Montpellier"],
    }) + "\n```"


def stratege(_):
    return json.dumps({
        "queries_generales": [],
        "queries_linkedin": [],
        "queries_github": ["python fastapi location:Montpellier"],
        "queries_cv_sites": [],
        "tags_stackoverflow": [],
    })


def evaluateur(texte):
    if "Carol" in texte:
        return "Désolé, je ne peux pas répondre au format demandé."
    score = 88 if "Alice" in texte else 25
    return "Voici mon évaluation : " + json.dumps({
        "score_global": score,
        "scores_detail": {"hard_skills": score, "soft_skills": 70, "experience": score, "culture_fit": 70},
        "resume": "Profil évalué.",
    })


def verificateur(texte):
    data = json.loads(texte[texte.index("Candidat à vérifier :") + len("Candidat à vérifier :"):texte.index("Contrôle")])
    valide = data["score_global_A4"] >= 50
    return json.dumps({
        "candidat_id": data["candidat_id"],
        "nom": data["nom"],
        "score_final": data["score_global_A4"],
        "statut": "valide" if valide else "invalide",
        "remarques": "Compétences Python, FastAPI et Docker confirmées." if valide else "Stack front-end.",
    })


def recruteur(texte):
    return json.dumps([{
        "candidat_id": "x",
        "nom": "Alice Martin",
        "objet": "Poste de développeuse backend Python",
        "message": "Bonjour Alice, votre expérience FastAPI nous intéresse.",
        "canal": "email",
    }])


@pytest.fixture
def pipeline(tmp_path):
    from src.tools import rag

    hits = [
        {"url": url, "title": nom, "body": texte[:120], "source": "github"}
        for url, (nom, texte) in PROFILS.items()
    ]
    llms = {
        "analyste": FakeLLM(analyste),
        "chercheur_stratege": FakeLLM(stratege),
        "evaluateur": FakeLLM(evaluateur),
        "verificateur": FakeLLM(verificateur),
        "recruteur": FakeLLM(recruteur),
    }
    patches = [
        mock.patch.object(rag, "_instance", rag.MemoireRAG(str(tmp_path / "chroma"))),
        mock.patch("src.agents.chercheur_collecteur._ddg_search_raw", return_value=[]),
        mock.patch("src.agents.chercheur_collecteur.search_github_users", return_value=hits),
        mock.patch("src.agents.chercheur_collecteur.search_stackoverflow_users", return_value=[]),
        mock.patch("src.agents.chercheur_collecteur._DDG_DELAY", 0),
        mock.patch(
            "src.agents.chercheur_filtre.extraire_page_web_raw",
            side_effect=lambda url: PROFILS[url][1],
        ),
    ] + [
        mock.patch(f"src.agents.{module}.get_llm", return_value=llm)
        for module, llm in llms.items()
    ]
    for p in patches:
        p.start()
    try:
        from src.graph import build_graph

        app = build_graph(with_interrupt=False)
        state = app.invoke(
            {"fiche_poste": FICHE},
            config={"configurable": {"thread_id": "test-offline"}, "max_concurrency": 2},
        )
        yield state, llms
    finally:
        for p in reversed(patches):
            p.stop()


def test_pipeline_complet_jusqu_au_rapport(pipeline):
    state, _ = pipeline
    assert state["profil_competences"]["hard_skills"][0] == "Python"
    assert len(state["profils_dedupliques"]) == 3
    assert len(state["candidats_scores"]) == 3          # fan-out A4 × 3
    assert len(state["candidats_valides"]) == 3         # fan-out A5 × 3
    assert state["rapport_final"]


def test_seul_le_bon_candidat_est_retenu_et_contacte(pipeline):
    state, _ = pipeline
    par_nom = {c["nom"]: c for c in state["candidats_valides"]}
    assert par_nom["Alice Martin"]["statut"] == "valide"
    assert par_nom["Bob Durand"]["statut"] == "invalide"
    assert [m["nom"] for m in state["messages_envoyes"]] == ["Alice Martin"]


def test_reponse_llm_inexploitable_ne_produit_pas_de_score_invente(pipeline):
    state, llms = pipeline
    carol = next(s for s in state["candidats_scores"] if s["nom"] == "Carol Petit")
    assert carol["score_global"] == 0.0
    assert carol["scores_detail"].get("evaluation_echouee") is True
    verif = next(c for c in state["candidats_valides"] if c["nom"] == "Carol Petit")
    assert verif["statut"] == "invalide"
    # 1 appel pour Alice, 1 pour Bob, 2 tentatives pour Carol
    assert llms["evaluateur"].appels == 4


def test_candidats_valides_memorises_dans_le_rag(pipeline):
    from src.tools.rag import get_memoire

    assert get_memoire().compter_fiches() == 1
    assert get_memoire().compter() >= 1
