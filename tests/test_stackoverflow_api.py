"""Recherche Stack Overflow : bon endpoint et répartition entre les tags."""

from unittest import mock

from src.tools import stackoverflow_api


def _reponse(items):
    resp = mock.Mock()
    resp.json.return_value = {"items": items}
    resp.raise_for_status.return_value = None
    return resp


def _top(*users):
    return [{"user": {"user_id": uid, "user_type": "registered"}, "post_count": n} for uid, n in users]


def fake_get(url, params=None, timeout=None):
    if url.endswith("/tags/fastapi/top-answerers/all_time"):
        return _reponse(_top((1, 300), (2, 100)))
    if url.endswith("/tags/python/top-answerers/all_time"):
        return _reponse(_top((10, 20000), (11, 9000)))
    if "/users/" in url:
        ids = [int(i) for i in url.rsplit("/", 1)[1].split(";")]
        return _reponse([
            {"user_id": i, "display_name": f"Dev {i}", "link": f"https://stackoverflow.com/users/{i}",
             "reputation": 1000 * i, "location": "Montpellier, France" if i == 1 else ""}
            for i in ids
        ])
    raise AssertionError(f"URL inattendue : {url}")


def test_utilise_les_meilleurs_contributeurs_du_tag():
    with mock.patch.object(stackoverflow_api.requests, "get", side_effect=fake_get) as get:
        hits = stackoverflow_api.search_stackoverflow_users(["FastAPI", "python"], max_results=4)
    appels = [c.args[0] for c in get.call_args_list]
    assert not any(url.endswith("/2.3/users") for url in appels)  # /users ignore les tags
    assert [h["title"] for h in hits] == ["Dev 1", "Dev 10", "Dev 2", "Dev 11"]


def test_le_profil_contient_les_contributions_et_la_localisation():
    with mock.patch.object(stackoverflow_api.requests, "get", side_effect=fake_get):
        hits = stackoverflow_api.search_stackoverflow_users(["fastapi"], max_results=1)
    assert hits[0]["source"] == "stackoverflow"
    assert "fastapi (300 réponses)" in hits[0]["body"]
    assert "Montpellier" in hits[0]["body"]


def test_api_indisponible_renvoie_une_liste_vide():
    with mock.patch.object(stackoverflow_api.requests, "get", side_effect=ConnectionError("réseau")):
        assert stackoverflow_api.search_stackoverflow_users(["python"]) == []
