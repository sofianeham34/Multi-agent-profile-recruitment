"""Appels LLM qui doivent renvoyer du JSON.

Les modèles ajoutent parfois des balises Markdown (```json … ```) ou une phrase
avant l'objet. Ce module centralise l'extraction et relance le modèle si la
réponse reste inexploitable, au lieu de laisser chaque agent inventer une
valeur par défaut.
"""

import json
import re

from src.logger import get_logger

_log = get_logger("llm_json")


class LLMJsonError(ValueError):
    """Le LLM n'a pas produit de JSON exploitable après plusieurs tentatives."""


def parse_json(content: str):
    """Extrait le premier objet ou tableau JSON d'une réponse de LLM."""
    text = (content or "").strip()
    if not text:
        raise ValueError("Réponse vide")

    candidats = [text]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        candidats.append(fence.group(1).strip())
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            candidats.append(match.group(0))

    for candidat in candidats:
        try:
            return json.loads(candidat)
        except json.JSONDecodeError:
            continue
    raise ValueError("Aucun JSON valide trouvé")


def invoke_json(llm, messages, *, expected=dict, attempts: int = 2, label: str = "LLM"):
    """Appelle le LLM et renvoie (données JSON, réponse brute).

    expected : dict ou list — type attendu ; un objet unique est accepté et
    encapsulé si une liste est attendue.
    Lève LLMJsonError si aucune tentative ne donne un JSON du bon type.
    """
    derniere_erreur = None
    for tentative in range(1, attempts + 1):
        response = llm.invoke(messages)
        try:
            data = parse_json(getattr(response, "content", response))
            if expected is list and isinstance(data, dict):
                data = [data]
            if not isinstance(data, expected):
                raise ValueError(f"JSON de type {type(data).__name__}, {expected.__name__} attendu")
            return data, response
        except ValueError as exc:
            derniere_erreur = exc
            _log.warning("%s : JSON invalide (tentative %d/%d) : %s", label, tentative, attempts, exc)
    raise LLMJsonError(f"{label} : pas de JSON exploitable après {attempts} tentatives ({derniere_erreur})")
