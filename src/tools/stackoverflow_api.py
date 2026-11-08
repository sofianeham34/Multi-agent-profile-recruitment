"""
Stack Overflow / Stack Exchange API — Recherche de développeurs.

API publique 100% gratuite :
    - Sans clé  : 300 req/jour
    - Avec clé  : 10 000 req/jour (clé gratuite sur stackapps.com)

Stratégie : chercher les meilleurs contributeurs d'un tag donné
(python, fastapi, django...) — ce sont des devs réels avec un profil
vérifiable, pas des bots ni des offres d'emploi.

Usage :
    from src.tools.stackoverflow_api import search_stackoverflow_users
    hits = search_stackoverflow_users(["python", "machine-learning"], max_results=5)
"""

import os

import requests
from bs4 import BeautifulSoup

from src.logger import get_logger

_log = get_logger("tools.stackoverflow")
_SO_API = "https://api.stackexchange.com/2.3"


def _params(**extra) -> dict:
    params = {"site": "stackoverflow", **extra}
    # Clé optionnelle pour augmenter le quota (gratuite sur stackapps.com)
    so_key = os.getenv("STACKOVERFLOW_KEY", "")
    if so_key:
        params["key"] = so_key
    return params


def search_stackoverflow_users(tags: list[str], max_results: int = 5) -> list[dict]:
    """Recherche les meilleurs contributeurs Stack Overflow sur des tags donnés.

    Utilise /tags/{tag}/top-answerers (l'endpoint /users ne sait pas filtrer
    par tag et renverrait les utilisateurs les plus réputés du site entier).

    Args:
        tags: Liste de tags SO (ex: ["python", "fastapi"]).
        max_results: Nombre max de profils retournés.

    Returns:
        Liste de dicts {title, url, body, source} — même format que
        _ddg_search_raw pour intégration transparente dans A3b.
    """
    tags = [t.lower().strip().replace(" ", "-") for t in tags if t and t.strip()][:3]
    if not tags:
        return []

    # 1) Meilleurs contributeurs de chaque tag (score de réponses sur le tag)
    contributeurs: dict[int, dict] = {}
    classements: list[list[int]] = []
    for tag in tags:
        try:
            resp = requests.get(
                f"{_SO_API}/tags/{tag}/top-answerers/all_time",
                params=_params(pagesize=min(max_results, 10)),
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            _log.warning("Stack Overflow API indisponible pour le tag %s : %s", tag, e)
            continue
        classement = []
        for item in items:
            user = item.get("user", {})
            uid = user.get("user_id")
            if not uid or user.get("user_type") == "does_not_exist":
                continue
            entry = contributeurs.setdefault(uid, {"tags": {}})
            entry["tags"][tag] = item.get("post_count", 0)
            classement.append(uid)
        classements.append(classement)

    if not contributeurs:
        return []

    # Répartition équitable entre les tags : un tag très général (python)
    # n'écrase pas un tag spécifique (fastapi), dont les experts sont plus rares.
    ids: list[int] = []
    for rang in range(max(len(c) for c in classements)):
        for classement in classements:
            if rang < len(classement) and classement[rang] not in ids:
                ids.append(classement[rang])
    ids = ids[:max_results]

    # 2) Profils détaillés (localisation, bio, site) en une seule requête
    try:
        resp = requests.get(
            f"{_SO_API}/users/{';'.join(str(u) for u in ids)}",
            params=_params(filter="!nNPvSNdWme", pagesize=len(ids)),
            timeout=10,
        )
        resp.raise_for_status()
        users = {u["user_id"]: u for u in resp.json().get("items", [])}
    except Exception as e:
        _log.warning("Stack Overflow API : profils détaillés indisponibles : %s", e)
        users = {}

    _log.info("Stack Overflow API : %d contributeurs pour tags=[%s]", len(ids), ";".join(tags))

    results = []
    for uid in ids:
        user = users.get(uid, {})
        name = user.get("display_name", f"Utilisateur {uid}")
        about = user.get("about_me", "")
        if about:
            about = BeautifulSoup(about, "html.parser").get_text(separator=" ", strip=True)[:500]

        activite = ", ".join(f"{tag} ({n} réponses)" for tag, n in contributeurs[uid]["tags"].items())
        body_parts = [f"Réputation Stack Overflow: {user.get('reputation', 0)}", f"Contributions: {activite}"]
        if user.get("location"):
            body_parts.append(f"Localisation: {user['location']}")
        if about:
            body_parts.append(f"À propos: {about}")
        if user.get("website_url"):
            body_parts.append(f"Site: {user['website_url']}")

        results.append({
            "title": name,
            "url": user.get("link", f"https://stackoverflow.com/users/{uid}"),
            "body": "\n".join(body_parts),
            "source": "stackoverflow",
        })

    return results
