"""
Tests — Structure du graphe et routage conditionnel.

Verifie la topologie du graphe et la logique de routage sans LLM.
"""

import pytest
from src.graph import build_graph, route_apres_verification
from src.state import CandidatValide


def _valide(nom="Alice", score=80.0, statut="valide") -> CandidatValide:
    return CandidatValide(
        candidat_id="x1", nom=nom,
        score_final=score, statut=statut, remarques=""
    )


class TestStructureGraphe:
    @pytest.fixture(scope="class")
    @classmethod
    def app(cls):
        return build_graph(with_interrupt=False)

    def test_noeuds_presents(self, app):
        noeuds = set(app.get_graph().nodes.keys())
        attendus = {
            "orchestrateur", "analyste",
            "chercheur_stratege", "chercheur_collecteur", "chercheur_filtre",
            "deduplicateur", "evaluateur", "reduce_scores",
            "verificateur", "reduce_validations", "recruteur", "rapport", "persistance",
        }
        assert attendus.issubset(noeuds)

    def test_noeud_persistance_present(self, app):
        noeuds = set(app.get_graph().nodes.keys())
        assert "persistance" in noeuds

    def test_noeuds_au_total(self, app):
        # __start__ + 13 nœuds métier (dont injection_rag) + persistance + __end__ = 16
        assert len(app.get_graph().nodes) == 16

    def test_aretes_pipeline_a3(self, app):
        """A3a -> A3b -> A3c -> deduplicateur."""
        edges = {(e[0], e[1]) for e in app.get_graph().edges}
        assert ("chercheur_stratege", "chercheur_collecteur") in edges
        assert ("chercheur_collecteur", "chercheur_filtre") in edges
        assert ("chercheur_filtre", "deduplicateur") in edges

    def test_arete_rapport_persistance(self, app):
        edges = {(e[0], e[1]) for e in app.get_graph().edges}
        assert ("rapport", "persistance") in edges

    def test_arete_verificateur_reduce_validations(self, app):
        edges = {(e[0], e[1]) for e in app.get_graph().edges}
        assert ("verificateur", "reduce_validations") in edges

    def test_interrupt_before_recruteur(self):
        app_interrupt = build_graph(with_interrupt=True)
        # Pas d'assertion directe sur le config LangGraph, on verifie juste la compilation
        assert app_interrupt is not None


class TestRoutageConditionnel:
    def test_score_eleve_route_recruteur(self):
        state = {"candidats_valides": [_valide(score=80.0)]}
        assert route_apres_verification(state) == "recruteur"

    def test_score_exactement_75_route_recruteur(self):
        state = {"candidats_valides": [_valide(score=75.0)]}
        assert route_apres_verification(state) == "recruteur"

    def test_score_viable_route_recruteur_relatif(self):
        """Score entre 40 et 75 -> recruteur (mode relatif top-3)."""
        state = {"candidats_valides": [_valide(score=60.0)]}
        assert route_apres_verification(state) == "recruteur"

    def test_score_sous_viable_route_rapport(self):
        """Score < 40 -> rapport, aucun viable."""
        state = {"candidats_valides": [_valide(score=30.0)]}
        assert route_apres_verification(state) == "rapport"

    def test_aucun_candidat_route_rapport(self):
        state = {"candidats_valides": []}
        assert route_apres_verification(state) == "rapport"

    def test_meilleur_score_determine_route(self):
        """Avec plusieurs candidats, c'est le meilleur score qui compte."""
        state = {"candidats_valides": [
            _valide(score=30.0),
            _valide(score=80.0),   # celui-ci depasse le seuil
            _valide(score=45.0),
        ]}
        assert route_apres_verification(state) == "recruteur"

    def test_tous_sous_seuil_mais_viables(self):
        """Tous sous 75 mais au-dessus de 40 -> recruteur relatif."""
        state = {"candidats_valides": [
            _valide(score=68.0),
            _valide(score=55.0),
        ]}
        assert route_apres_verification(state) == "recruteur"

    def test_tous_invalides_score_zero(self):
        """Tous invalides avec score 0 -> rapport."""
        state = {"candidats_valides": [
            _valide(score=0.0, statut="invalide"),
            _valide(score=5.0, statut="invalide"),
        ]}
        assert route_apres_verification(state) == "rapport"

    def test_douteux_viable_passe_par_la_validation_humaine(self):
        """Un douteux viable est proposé à l'humain (l'interrupt précède A7)."""
        state = {"candidats_valides": [
            _valide(score=90.0, statut="douteux"),
        ]}
        assert route_apres_verification(state) == "recruteur"

    def test_douteux_trop_faible_va_au_rapport(self):
        state = {"candidats_valides": [
            _valide(score=30.0, statut="douteux"),
        ]}
        assert route_apres_verification(state) == "rapport"

    def test_seuls_les_valides_comptent_pour_le_routage(self):
        state = {"candidats_valides": [
            _valide(score=95.0, statut="douteux"),
            _valide(score=60.0, statut="valide"),
        ]}
        assert route_apres_verification(state) == "recruteur"


def test_selection_evaluation_lieu_puis_alternance_des_sources():
    from src.graph import selection_evaluation

    profils = (
        [{"nom": f"li{i}", "source": "linkedin", "profil_brut": "Python dev"} for i in range(6)]
        + [{"nom": "gh_mtp", "source": "github", "profil_brut": "Localisation: Montpellier"},
           {"nom": "gh2", "source": "github", "profil_brut": "Python"},
           {"nom": "so1", "source": "stackoverflow", "profil_brut": "Python"}]
    )
    choisis = [p["nom"] for p in selection_evaluation(profils, {"localisations": ["Montpellier"]}, 5)]
    assert choisis[0] == "gh_mtp"  # lieu confirmé en premier
    assert {"gh2", "so1"} <= set(choisis)  # chaque source a sa place
    assert len(choisis) == 5


def test_selection_evaluation_garde_tout_si_assez_de_places():
    from src.graph import selection_evaluation

    profils = [{"nom": "a", "source": "linkedin", "profil_brut": ""}]
    assert selection_evaluation(profils, {}, 5) == profils


def test_etat_courant_derniere_decision_l_emporte():
    from src.state import etat_courant

    liste = [
        {"candidat_id": "a", "statut": "valide"},
        {"candidat_id": "b", "statut": "valide"},
        {"candidat_id": "a", "statut": "invalide"},
    ]
    assert etat_courant(liste) == [{"candidat_id": "b", "statut": "valide"}, {"candidat_id": "a", "statut": "invalide"}]


def test_recruteur_ignore_un_candidat_rejete_par_l_humain():
    from unittest import mock

    from src.agents.recruteur import recruteur_node

    alice = {"candidat_id": "c1", "nom": "Alice", "score_final": 90.0, "statut": "valide",
             "remarques": "", "source": "github", "url": None}
    etat = {"fiche_poste": "x", "candidats_valides": [alice, {**alice, "statut": "invalide"}]}
    llm = mock.MagicMock()
    with mock.patch("src.agents.recruteur.get_llm", return_value=llm):
        assert recruteur_node(etat) == {"messages_envoyes": []}
    llm.invoke.assert_not_called()


def test_recruteur_ne_contacte_jamais_un_douteux_non_coche():
    """Sans accord humain (statut resté « douteux »), aucun message n'est rédigé."""
    from unittest import mock

    from src.agents.recruteur import recruteur_node

    bob = {"candidat_id": "c2", "nom": "Bob", "score_final": 90.0, "statut": "douteux",
           "remarques": "", "source": "github", "url": None}
    llm = mock.MagicMock()
    with mock.patch("src.agents.recruteur.get_llm", return_value=llm):
        assert recruteur_node({"fiche_poste": "x", "candidats_valides": [bob]}) == {"messages_envoyes": []}
    llm.invoke.assert_not_called()


def test_routage_apres_rejet_humain_va_au_rapport():
    from src.graph import route_apres_verification

    alice = {"candidat_id": "c1", "nom": "Alice", "score_final": 90.0, "statut": "valide"}
    etat = {"candidats_valides": [alice, {**alice, "statut": "invalide"}]}
    assert route_apres_verification(etat) == "rapport"


def test_metriques_exportees_en_fin_de_pipeline():
    import glob
    import os

    from src.agents.persistance import persistance_node

    avant = set(glob.glob(os.path.join(os.environ["METRICS_DIR"], "metriques_*.json")))
    persistance_node({"candidats_valides": [], "profils_dedupliques": [], "fiche_poste": ""})
    apres = set(glob.glob(os.path.join(os.environ["METRICS_DIR"], "metriques_*.json")))
    assert apres - avant


def test_recruteur_redige_pour_toute_la_selection_humaine():
    """Les candidats cochés ont tous un message, même sous le seuil ou au-delà du top-3."""
    from unittest import mock

    from src.agents.recruteur import recruteur_node

    coches = [
        {"candidat_id": f"c{i}", "nom": f"N{i}", "score_final": s, "statut": "valide",
         "remarques": "", "source": "github", "url": None, "choix_humain": True}
        for i, s in enumerate([35.0, 48.0, 43.0, 41.0, 60.0])
    ]
    non_coche = {"candidat_id": "z", "nom": "Z", "score_final": 90.0, "statut": "invalide",
                 "remarques": "", "source": "github", "url": None}
    capture = {}

    def faux_invoke(llm, messages, **kw):
        capture["prompt"] = messages[-1].content
        return [{"candidat_id": c["candidat_id"], "nom": c["nom"], "message": "x"} for c in coches], None

    with mock.patch("src.agents.recruteur.get_llm"), \
         mock.patch("src.agents.recruteur.invoke_json", side_effect=faux_invoke):
        res = recruteur_node({"fiche_poste": "x", "candidats_valides": coches + [non_coche]})
    assert len(res["messages_envoyes"]) == 5
    assert all(f"N{i}" in capture["prompt"] for i in range(5))
    assert '"Z"' not in capture["prompt"]
