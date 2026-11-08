"""A2 — Analyste : extrait le profil de compétences depuis la fiche de poste."""

import re
from langchain_core.messages import SystemMessage, HumanMessage

from src.state import GraphState
from src.config import get_llm
from src.llm_json import invoke_json
from src.prompts import ANALYSTE_SYSTEM
from src.observabilite import get_metrics
from src.logger import get_logger

_log = get_logger("A2_analyste")


def _inferer_niveau_experience(fiche_poste: str) -> dict:
    """Détecte le niveau d'expérience depuis la fiche."""
    texte = fiche_poste.lower()
    if any(mot in texte for mot in ("alternance", "alternant", "apprentissage", "apprenti")):
        return {
            "niveau_experience": "alternant",
            "type_contrat": "alternance",
            "experience_min": 0,
            "experience_max": 1,
        }
    if any(mot in texte for mot in ("stage", "stagiaire", "internship")):
        return {
            "niveau_experience": "stagiaire",
            "type_contrat": "stage",
            "experience_min": 0,
            "experience_max": 1,
        }
    if "junior" in texte or "débutant" in texte or "debutant" in texte:
        return {
            "niveau_experience": "junior",
            "type_contrat": "indifferent",
            "experience_min": 0,
            "experience_max": 2,
        }
    if "étudiant" in texte or "etudiant" in texte or "master" in texte:
        return {
            "niveau_experience": "junior",
            "type_contrat": "indifferent",
            "experience_min": 0,
            "experience_max": 2,
        }
    if "senior" in texte or "lead" in texte:
        return {
            "niveau_experience": "senior",
            "type_contrat": "indifferent",
            "experience_min": 5,
            "experience_max": None,
        }
    if "confirmé" in texte or "confirme" in texte:
        return {
            "niveau_experience": "confirme",
            "type_contrat": "indifferent",
            "experience_min": 3,
            "experience_max": 5,
        }
    return {
        "niveau_experience": "indifferent",
        "type_contrat": "indifferent",
        "experience_min": None,
        "experience_max": None,
    }


def _inferer_localisations(fiche_poste: str) -> list[str]:
    """Extrait les lieux cités dans la fiche."""
    pattern = re.compile(
        r"(?:\bà\b|\ba\b|\bsur\b|près de|pres de|proche de|autour de|basé à|base a|localisé à|localise a)\s+"
        r"([A-ZÉÈÀÂÎÔÛÇ][A-Za-zÀ-ÖØ-öø-ÿ' -]{1,45})"
    )
    lieux: list[str] = []
    stop = {
        "un", "une", "des", "le", "la", "les", "du", "de", "en", "avec",
        "pour", "poste", "profil", "alternance", "stage", "cdi", "cdd",
        # Plateformes cloud et techs souvent précédées de "sur"
        "aws", "gcp", "azure", "cloud", "kubernetes", "docker", "kubernetes",
        "github", "gitlab", "heroku", "vercel", "netlify",
    }
    for match in pattern.finditer(fiche_poste):
        lieu = re.split(r"[,.;:\n]", match.group(1))[0].strip(" -")
        mots = lieu.split()
        while mots and mots[-1].lower() in stop:
            mots.pop()
        lieu = " ".join(mots).strip()
        if lieu and lieu.lower() not in stop and lieu not in lieux:
            lieux.append(lieu)
    return lieux[:5]


def analyste_node(state: GraphState) -> dict:
    """Analyse la fiche de poste et extrait le profil de compétences."""
    m = get_metrics()
    m.debut("A2_analyste")
    _log.info("Analyse de la fiche de poste en cours...")
    llm = get_llm(temperature=0)

    messages = [
        SystemMessage(content=ANALYSTE_SYSTEM),
        HumanMessage(content=f"Fiche de poste :\n\n{state['fiche_poste']}")
    ]

    # Sans profil de compétences, la suite du pipeline n'a pas de sens :
    # en cas d'échec répété, le run s'arrête avec une erreur explicite.
    profil, response = invoke_json(llm, messages, expected=dict, attempts=3, label="A2 analyste")

    signaux = _inferer_niveau_experience(state["fiche_poste"])
    ancien_niveau = profil.pop("niveau_seniorite", None)
    if profil.get("niveau_experience") in (None, "", "indifferent"):
        profil["niveau_experience"] = (
            signaux["niveau_experience"]
            if signaux["niveau_experience"] != "indifferent"
            else ancien_niveau or "indifferent"
        )
    if profil.get("type_contrat") in (None, "", "indifferent"):
        profil["type_contrat"] = signaux["type_contrat"]

    if signaux["experience_min"] is not None:
        profil["experience_min"] = signaux["experience_min"]
    else:
        try:
            profil["experience_min"] = int(profil.get("experience_min") or 0)
        except (TypeError, ValueError):
            profil["experience_min"] = 0
    if signaux["experience_max"] is not None:
        profil["experience_max"] = signaux["experience_max"]
    elif "experience_max" not in profil:
        profil["experience_max"] = None

    localisations = profil.get("localisations") or _inferer_localisations(state["fiche_poste"])
    profil["localisations"] = localisations
    profil["remote"] = any(
        mot in state["fiche_poste"].lower()
        for mot in ("remote", "télétravail", "teletravail", "hybride")
    )

    mots_cles = list(profil.get("mots_cles", []))
    for mot in (
        profil.get("niveau_experience"),
        profil.get("type_contrat"),
        *profil.get("localisations", []),
    ):
        if mot and mot != "indifferent" and mot not in mots_cles:
            mots_cles.append(mot)
    profil["mots_cles"] = mots_cles

    n_hard = len(profil.get('hard_skills', []))
    n_soft = len(profil.get('soft_skills', []))
    _log.info("Profil extrait : %d hard skills, %d soft skills, %d mots-clés.", n_hard, n_soft, len(profil.get('mots_cles', [])))
    m.fin("A2_analyste", n_hard_skills=n_hard, n_soft_skills=n_soft)

    return {
        "profil_competences": profil,
        "messages": [response]
    }
