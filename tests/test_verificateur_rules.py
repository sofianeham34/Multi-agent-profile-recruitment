from src.agents.verificateur import (
    _adequation_minimale,
    _localisation_confirmee,
    revalider_candidat_connu,
    _profil_non_candidat,
    _profil_personne_probable,
)
from src.llm_json import parse_json


def test_pages_non_candidats_detectees_depuis_titre_et_url():
    assert _profil_non_candidat(
        "Angular vs React : Quel framework front-end choisir ?",
        "",
        "https://www.aquilapp.fr/ressources/projet-web/angular-vs-react",
    )
    assert _profil_non_candidat(
        "CV PYTHON : recevez gratuitement les...",
        "",
        "https://www.freelance-informatique.fr/cv-python-622",
    )


def test_profils_personnes_probables_conserves():
    assert _profil_personne_probable(
        "Erwann B. - Epitech Student at Epitech Montpellier | LinkedIn",
        "Étudiant à Epitech Montpellier, projets Java et React.",
        "linkedin",
        "https://fr.linkedin.com/in/erwann-b-874054197/en",
    )
    assert _profil_personne_probable(
        "Charalambos Anastassopoulos, Développeur JAVA Angular / freelance",
        "Développeur Java Angular freelance.",
        "web",
        "https://www.malt.fr/profile/charalambosanastassopoulos",
    )


def test_adequation_minimale_accepte_profil_tech_avec_competences():
    # _adequation_minimale ignore les profils < 400 chars (scraping échoué).
    # On teste avec des profils suffisamment longs pour déclencher l'analyse.
    profil_requis = {"hard_skills": ["Java", "React"], "niveau_experience": "junior"}
    profil_long_tech = (
        "Étudiant à Epitech Montpellier, projets Java et React. "
        "Développeur junior, maîtrise Java Spring et React.js. "
        "Stage chez une startup, contribution open source. " * 5
    )
    ok, _ = _adequation_minimale(profil_requis, "Erwann B. - Epitech", profil_long_tech)
    assert ok

    profil_long_hors_sujet = (
        "Angular vs React : Quel framework front-end choisir ? "
        "Comparatif des frameworks JavaScript. Tutoriel formation. " * 5
    )
    ok, _ = _adequation_minimale(
        profil_requis,
        "Angular vs React : Quel framework front-end choisir ?",
        profil_long_hors_sujet,
    )
    assert not ok


def test_adequation_minimale_rejette_personne_hors_poste():
    profil_requis = {
        "hard_skills": ["Python", "FastAPI", "Docker", "RAG"],
        "niveau_experience": "senior",
    }

    profil_banker_long = (
        "Senior banker retail construction finance communication. "
        "Directeur commercial banque immobilier gestion patrimoine. " * 5
    )
    ok, remarque = _adequation_minimale(
        profil_requis,
        "Fabienne Brandsma - Senior Banker - Retail",
        profil_banker_long,
    )
    assert not ok
    assert "Compétences insuffisamment prouvées" in remarque

    profil_dev_long = (
        "Lead developer Python FastAPI Docker. Architecte logiciel backend. "
        "Expérience RAG LangChain ChromaDB. Microservices Python senior. " * 5
    )
    ok, _ = _adequation_minimale(
        profil_requis,
        "Nicolas Bigot - Lead developer, architect - DoYouBuzz",
        profil_dev_long,
    )
    assert ok


def test_parse_validation_json_accepte_markdown_et_texte():
    parsed = parse_json(
        'Voici le résultat:\n```json\n{"candidat_id":"x","nom":"Alice","score_final":80,"statut":"valide","remarques":"ok"}\n```'
    )
    assert parsed["statut"] == "valide"


def test_localisation_confirmee_par_ville_france_ou_linkedin_fr():
    requis = {"localisations": ["Montpellier"]}
    assert _localisation_confirmee(requis, "Alice", "Développeuse Python à Montpellier", "")[0]
    assert _localisation_confirmee(requis, "Bob", "Backend Python, Lyon, France", "")[0]
    assert _localisation_confirmee(requis, "Chloé", "Backend Python", "https://fr.linkedin.com/in/chloe")[0]


def test_localisation_etrangere_ou_absente_non_confirmee():
    requis = {"localisations": ["Montpellier"]}
    ok, raison = _localisation_confirmee(requis, "Anna", "Python FastAPI", "https://in.linkedin.com/in/anna")
    assert not ok and "autre pays" in raison
    ok, raison = _localisation_confirmee(requis, "Raj", "Senior Python FastAPI developer", "https://www.linkedin.com/in/raj")
    assert not ok and "non confirmée" in raison


def test_sans_localisation_demandee_tout_passe():
    assert _localisation_confirmee({"localisations": []}, "X", "Python", "https://in.linkedin.com/in/x")[0]


_DATA_SCIENTIST = (
    "Data Scientist freelance à Montpellier, spécialisé en forecasting, analyse causale et optimisation. "
    "J'interviens sur l'ensemble du cycle de vie d'un projet data : compréhension du besoin, préparation des "
    "données, modélisation, évaluation, industrialisation et restitution des résultats. Je conçois les briques "
    "de mise en production : pipelines de données, orchestration, API, automatisation, déploiement et monitoring. "
    "Environnement technique principal : Python, SQL, dbt, Airflow, FastAPI, Docker, AWS et Power BI. "
    "Industrialisation avec Git, tests automatisés et pipelines CI/CD."
)


def test_candidat_memoire_ecarte_si_la_nouvelle_fiche_demande_d_autres_competences():
    # Validé pour « Python, FastAPI, Docker » : ne doit pas être repris pour une fiche LangGraph + RAG.
    requis = {"hard_skills": ["Python", "FastAPI", "LangGraph", "Docker", "RAG"],
              "localisations": ["Montpellier"], "niveau_experience": "senior"}
    ok, raison = revalider_candidat_connu(requis, "Thibault", _DATA_SCIENTIST, "https://fr.linkedin.com/in/t")
    assert not ok and "LangGraph" in raison


def test_candidat_memoire_repris_si_la_fiche_correspond():
    profil = "Développeur backend à Montpellier. " + _DATA_SCIENTIST.split(". ", 1)[1]
    requis = {"hard_skills": ["Python", "FastAPI", "Docker"], "localisations": ["Montpellier"],
              "niveau_experience": "indifferent"}
    ok, _ = revalider_candidat_connu(requis, "Léa", profil, "https://fr.linkedin.com/in/lea")
    assert ok


def test_candidat_memoire_trop_court_jamais_repris():
    assert not revalider_candidat_connu({"hard_skills": ["Python"]}, "X", "Python", "")[0]
