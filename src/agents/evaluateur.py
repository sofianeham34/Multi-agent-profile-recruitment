"""
A4 — Évaluateur de Profil (×N instances).

Calcule un score de matching multicritères pour chaque candidat.
Massivement parallélisable via le pattern Send() de LangGraph —
N instances traitent N profils simultanément.
"""

import json
from langchain_core.messages import SystemMessage, HumanMessage

from src.state import CandidatScore
from src.config import get_llm
from src.llm_json import invoke_json, LLMJsonError
from src.prompts import EVALUATEUR_SYSTEM
from src.tools.rag import get_memoire
from src.observabilite import get_metrics
from src.logger import get_logger


def evaluateur_node(state: dict) -> dict:
    """Évalue UN candidat par rapport au profil de compétences.

    Note: Reçoit un state partiel via Send() contenant :
        - candidat: Candidat
        - profil_competences: dict
    """
    candidat = state["candidat"]
    profil = state["profil_competences"]
    fiche_poste = state.get("fiche_poste", "")
    log = get_logger("A4_evaluateur")
    m = get_metrics()
    m.debut(f"A4_{candidat['id']}")

    log.info("Évaluation de : %s (source: %s)", candidat['nom'], candidat['source'])

    # --- Cache RAG : si l'URL a déjà été évaluée pour un poste similaire,
    # on réutilise le score sans appeler le LLM. ---
    url = candidat.get("url") or ""
    if url:
        try:
            cache = get_memoire().get_score_cache(url, fiche_poste if fiche_poste else None)
            if cache is not None:
                log.info(
                    "Cache RAG : %s → %.1f/100 (score réutilisé, LLM skippé)",
                    candidat["nom"], cache["score"],
                )
                m.fin(f"A4_{candidat['id']}", candidat_nom=candidat['nom'],
                      score=cache["score"], source=candidat["source"], cache_hit=True)
                return {
                    "candidats_scores": [CandidatScore(
                        candidat_id=candidat["id"],
                        nom=candidat["nom"],
                        score_global=cache["score"],
                        scores_detail={},
                        resume=f"[Cache RAG] {cache['remarques'][:200]}" if cache["remarques"] else "[Cache RAG] Score issu d'un run précédent sur poste similaire.",
                    )]
                }
        except Exception as _e:
            log.debug("Cache RAG indisponible : %s", _e)

    # --- Contexte RAG : profils similaires, restreints aux fiches de poste
    # comparables à la fiche courante (évite la calibration inter-postes). ---
    contexte_rag = ""
    try:
        memoire = get_memoire()
        similaires = memoire.rechercher_similaires(
            candidat["profil_brut"],
            fiche_poste=fiche_poste if fiche_poste else None,
            n_results=2,
        )
        if similaires:
            lignes = ["Profils similaires évalués pour un poste comparable (à titre de référence) :"]
            for s in similaires:
                lignes.append(
                    f"  - {s['nom']} | score={s['score']}/100 | similarité={s['similarite']} | {s['remarques'][:100]}"
                )
            contexte_rag = "\n".join(lignes)
            log.info("RAG : %d profil(s) similaire(s) récupéré(s) pour calibration.", len(similaires))
    except Exception as e:
        log.warning("RAG indisponible : %s", e)

    llm = get_llm(temperature=0)

    eval_msg = f"""Profil de compétences requis :
{json.dumps(profil, ensure_ascii=False, indent=2)}

Profil du candidat :
Nom : {candidat['nom']}
Source : {candidat['source']}
Profil :
{candidat['profil_brut']}
{f"{chr(10)}{contexte_rag}" if contexte_rag else ""}
Évalue ce candidat par rapport au profil requis."""

    messages = [
        SystemMessage(content=EVALUATEUR_SYSTEM),
        HumanMessage(content=eval_msg)
    ]

    # Les erreurs 429 (limite de débit) sont relancées par get_llm().with_retry.
    # Si la réponse reste inexploitable, le candidat est marqué en échec avec
    # un score nul : il ne sera jamais contacté sur la base d'un score inventé.
    try:
        scores, response = invoke_json(llm, messages, expected=dict, label=f"A4 {candidat['nom'][:40]}")
    except LLMJsonError as exc:
        log.error("Évaluation impossible pour %s : %s", candidat['nom'], exc)
        m.fin(f"A4_{candidat['id']}", candidat_nom=candidat['nom'], score=0.0, source=candidat['source'], echec=True)
        return {
            "candidats_scores": [CandidatScore(
                candidat_id=candidat["id"],
                nom=candidat["nom"],
                score_global=0.0,
                scores_detail={"evaluation_echouee": True},
                resume=f"Évaluation impossible : réponse du LLM inexploitable pour {candidat['nom']}.",
            )]
        }

    score_global = max(0.0, min(100.0, float(scores.get("score_global", 0) or 0)))
    candidat_score = CandidatScore(
        candidat_id=candidat["id"],
        nom=candidat["nom"],
        score_global=score_global,
        scores_detail=scores.get("scores_detail", {}),
        resume=scores.get("resume", "")
    )

    log.info("%s -> score: %.1f/100", candidat['nom'], score_global)
    m.fin(f"A4_{candidat['id']}", candidat_nom=candidat['nom'], score=score_global, source=candidat['source'])

    return {
        "candidats_scores": [candidat_score]
    }
