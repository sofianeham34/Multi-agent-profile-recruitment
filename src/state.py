"""État partagé du graphe (blackboard SMA)."""

from typing import TypedDict, Annotated, Optional
from langgraph.graph import add_messages
import operator


class Candidat(TypedDict):
    id: str
    nom: str
    source: str                # linkedin, github, web, malt…
    profil_brut: str           # texte scrapé
    url: Optional[str]


class CandidatScore(TypedDict):
    candidat_id: str
    nom: str
    score_global: float        # 0-100
    scores_detail: dict
    resume: str


class CandidatValide(TypedDict):
    candidat_id: str
    nom: str
    score_final: float
    statut: str                # "valide", "invalide", "douteux"
    remarques: str
    source: Optional[str]
    url: Optional[str]


class GraphState(TypedDict):
    fiche_poste: str                                              # input
    profil_competences: dict                                      # A2
    requetes_recherche: dict                                      # A3a
    resultats_bruts: list[dict]                                   # A3b
    profils_bruts: Annotated[list[Candidat], operator.add]        # A3c
    profils_dedupliques: list[Candidat]                           # A6
    candidats_scores: Annotated[list[CandidatScore], operator.add]  # A4 ×N
    candidats_valides: Annotated[list[CandidatValide], operator.add] # A5 ×N
    messages_envoyes: Annotated[list[dict], operator.add]         # A7
    rapport_final: str                                            # A1
    messages: Annotated[list, add_messages]                       # trace LLM
