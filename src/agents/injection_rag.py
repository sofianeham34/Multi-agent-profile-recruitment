"""
Injection RAG — candidats connus depuis la mémoire vectorielle.

Nœud intercalé après reduce_validations : complète les résultats du run
courant avec des candidats déjà validés lors de runs précédents pour un
poste similaire.

Filtre : ne réinjecte pas un candidat dont l'URL est déjà dans les
résultats courants (évite les doublons).
"""

from src.state import GraphState, CandidatValide
from src.observabilite import get_metrics
from src.logger import get_logger

_log = get_logger("injection_rag")


def injection_rag_node(state: GraphState) -> dict:
    """Injecte les candidats connus du RAG absents des résultats courants."""
    m = get_metrics()
    m.debut("injection_rag")

    fiche_poste = state.get("fiche_poste", "")
    candidats_actuels = state.get("candidats_valides", [])

    # URLs et IDs déjà présents dans ce run
    urls_actuels = {c.get("url") for c in candidats_actuels if c.get("url")}
    ids_actuels = {c.get("candidat_id") for c in candidats_actuels}

    try:
        from src.tools.rag import get_memoire
        connus = get_memoire().get_candidats_connus(fiche_poste)
    except Exception as exc:
        _log.debug("RAG injection ignorée : %s", exc)
        m.fin("injection_rag", n_injectes=0)
        return {}

    from src.agents.verificateur import revalider_candidat_connu

    profil_requis = state.get("profil_competences", {})
    nouveaux: list[CandidatValide] = []
    n_ecartes = 0
    for c in connus:
        if c.get("url") in urls_actuels:
            continue
        if c["candidat_id"] in ids_actuels:
            continue
        # Validé pour une autre fiche : il doit satisfaire les exigences de celle-ci.
        ok, raison = revalider_candidat_connu(profil_requis, c["nom"], c.get("profil_brut", ""), c.get("url") or "")
        if not ok:
            n_ecartes += 1
            _log.info("RAG injection : %s écarté (%s).", c["nom"][:40], raison)
            continue
        nouveaux.append(CandidatValide(
            candidat_id=c["candidat_id"],
            nom=c["nom"],
            score_final=c["score"],
            statut="valide",
            remarques=f"[RAG] Candidat connu, revérifié pour cette fiche. {c['remarques'][:120]}".strip(),
            source=c.get("source") or "profil déjà récupéré",
            url=c.get("url") or None,
        ))

    if nouveaux:
        noms = ", ".join(f"{c['nom'][:25]} ({c['score_final']:.0f})" for c in nouveaux)
        _log.info("RAG injection : %d candidat(s) connu(s) ajouté(s) → %s", len(nouveaux), noms)
    else:
        _log.info("RAG injection : aucun nouveau candidat connu pour cette fiche.")

    m.fin("injection_rag", n_injectes=len(nouveaux), n_ecartes=n_ecartes)
    return {"candidats_valides": nouveaux} if nouveaux else {}
