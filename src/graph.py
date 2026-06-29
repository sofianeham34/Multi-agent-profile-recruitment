"""Graphe LangGraph du SMA de recrutement."""

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.memory import MemorySaver

from src.state import GraphState
from src.config import SCORE_SEUIL_CONTACT, SCORE_SEUIL_HUMAIN, SCORE_SEUIL_VIABLE, TOP_N_RELATIF, MAX_PROFILS_PARALLELES
from src.logger import get_logger
from src.observabilite import get_metrics

_log = get_logger("graph")

from src.agents.orchestrateur import orchestrateur_node, reduce_scores_node, reduce_validations_node, rapport_node
from src.agents.analyste import analyste_node
from src.agents.chercheur_stratege import stratege_node
from src.agents.chercheur_collecteur import collecteur_node
from src.agents.chercheur_filtre import filtre_node
from src.agents.deduplicateur import deduplicateur_node
from src.agents.evaluateur import evaluateur_node
from src.agents.verificateur import verificateur_node
from src.agents.recruteur import recruteur_node
from src.agents.persistance import persistance_node
from src.agents.injection_rag import injection_rag_node


def route_to_evaluateurs(state: GraphState) -> list[Send]:
    """Fan-out : un Send() par profil vers A4."""
    profils = state.get("profils_dedupliques", [])
    if not profils:
        _log.warning("Aucun profil à évaluer, passage direct à la réduction.")
        return [Send("reduce_scores", {})]

    # limite instances parallèles
    if len(profils) > MAX_PROFILS_PARALLELES:
        _log.warning(
            "%d profils disponibles, limite à %d évaluateurs parallèles (MAX_PROFILS_PARALLELES).",
            len(profils), MAX_PROFILS_PARALLELES,
        )
        profils = profils[:MAX_PROFILS_PARALLELES]

    _log.info("Fan-out : %d profils vers évaluateurs.", len(profils))
    return [
        Send("evaluateur", {
            "candidat": candidat,
            "profil_competences": state["profil_competences"],
            "fiche_poste": state.get("fiche_poste", ""),
        })
        for candidat in profils
    ]


def route_to_verificateurs(state: GraphState) -> list[Send]:
    """Fan-out : envoie chaque score A4 vers une instance A5 indépendante."""
    scores = state.get("candidats_scores", [])
    if not scores:
        _log.warning("Aucun score à vérifier, passage direct à la réduction A5.")
        return [Send("reduce_validations", {})]

    profils_par_id = {p["id"]: p for p in state.get("profils_dedupliques", [])}
    _log.info("Fan-out A5 : envoi de %d score(s) vers %d vérificateurs parallèles.", len(scores), len(scores))
    return [
        Send("verificateur", {
            "candidat_score": score,
            "profil_source": profils_par_id.get(score["candidat_id"], {}),
            "profil_competences": state["profil_competences"],
        })
        for score in scores
    ]


def route_apres_verification(state: GraphState) -> str:
    """Routage conditionnel après A5 : recruteur ou rapport."""
    candidats_valides = [
        c for c in state.get("candidats_valides", [])
        if c.get("statut") == "valide"
    ]

    if not candidats_valides:
        _log.info("Routage : aucun candidat validé -> rapport.")
        get_metrics().noter("routage", decision="rapport", raison="aucun_candidat")
        return "rapport"

    meilleur_score = max(c["score_final"] for c in candidats_valides)

    if meilleur_score >= SCORE_SEUIL_CONTACT:
        _log.info("Routage absolu : meilleur score %.1f >= %d -> A7 Recruteur.", meilleur_score, SCORE_SEUIL_CONTACT)
        get_metrics().noter("routage", decision="recruteur", mode="absolu", meilleur_score=meilleur_score)
        return "recruteur"

    # routage relatif : top-N si au moins un viable
    viables = [c for c in candidats_valides if c["score_final"] >= SCORE_SEUIL_VIABLE]
    if viables:
        top = sorted(viables, key=lambda x: x["score_final"], reverse=True)[:TOP_N_RELATIF]
        noms = ", ".join(f"{c['nom'][:30]} ({c['score_final']})" for c in top)
        _log.info(
            "Routage relatif : aucun >= %d, mais %d viable(s) >= %d. Top-%d : %s",
            SCORE_SEUIL_CONTACT, len(viables), SCORE_SEUIL_VIABLE, TOP_N_RELATIF, noms
        )
        get_metrics().noter("routage", decision="recruteur", mode="relatif", meilleur_score=meilleur_score)
        return "recruteur"

    _log.info("Routage : meilleur score %.1f < %d -> rapport (aucun viable).", meilleur_score, SCORE_SEUIL_VIABLE)
    get_metrics().noter("routage", decision="rapport", raison="score_insuffisant", meilleur_score=meilleur_score)
    return "rapport"


def build_graph(with_interrupt: bool = True) -> StateGraph:
    """Construit et compile le graphe."""
    graph = StateGraph(GraphState)

    graph.add_node("orchestrateur", orchestrateur_node)
    graph.add_node("analyste", analyste_node)
    graph.add_node("chercheur_stratege", stratege_node)       # A3a : requêtes
    graph.add_node("chercheur_collecteur", collecteur_node)   # A3b : collecte
    graph.add_node("chercheur_filtre", filtre_node)           # A3c : filtrage
    graph.add_node("deduplicateur", deduplicateur_node)
    graph.add_node("evaluateur", evaluateur_node)
    graph.add_node("reduce_scores", reduce_scores_node)
    graph.add_node("verificateur", verificateur_node)
    graph.add_node("reduce_validations", reduce_validations_node)
    graph.add_node("injection_rag", injection_rag_node)
    graph.add_node("recruteur", recruteur_node)
    graph.add_node("rapport", rapport_node)
    graph.add_node("persistance", persistance_node)

    # pipeline séquentiel
    graph.add_edge(START, "orchestrateur")
    graph.add_edge("orchestrateur", "analyste")
    graph.add_edge("analyste", "chercheur_stratege")
    graph.add_edge("chercheur_stratege", "chercheur_collecteur")
    graph.add_edge("chercheur_collecteur", "chercheur_filtre")
    graph.add_edge("chercheur_filtre", "deduplicateur")

    # fan-out parallèle A4
    graph.add_conditional_edges(
        "deduplicateur",
        route_to_evaluateurs,
        ["evaluateur", "reduce_scores"]
    )
    graph.add_edge("evaluateur", "reduce_scores")

    # fan-out parallèle A5
    graph.add_conditional_edges(
        "reduce_scores",
        route_to_verificateurs,
        ["verificateur", "reduce_validations"]
    )
    graph.add_edge("verificateur", "reduce_validations")

    graph.add_edge("reduce_validations", "injection_rag")

    # routage conditionnel
    graph.add_conditional_edges(
        "injection_rag",
        route_apres_verification,
        {
            "recruteur": "recruteur",
            "rapport": "rapport"
        }
    )

    graph.add_edge("recruteur", "rapport")
    graph.add_edge("rapport", "persistance")
    graph.add_edge("persistance", END)

    memory = MemorySaver()

    compile_kwargs = {"checkpointer": memory}
    if with_interrupt:
        compile_kwargs["interrupt_before"] = ["recruteur"]

    app = graph.compile(**compile_kwargs)
    return app
