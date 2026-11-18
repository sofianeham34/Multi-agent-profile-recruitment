from src.agents.chercheur_stratege import _enrichir_requetes


def test_requetes_operateurs_linkedin_et_cv():
    profil = {
        "hard_skills": ["Angular", "TypeScript"],
        "niveau_experience": "alternant",
        "type_contrat": "alternance",
        "localisations": ["Montpellier"],
        "remote": False,
    }

    requetes = _enrichir_requetes(
        {
            "queries_generales": [],
            "queries_linkedin": [],
            "queries_github": [],
            "queries_cv_sites": [],
            "tags_stackoverflow": [],
        },
        profil,
    )

    assert any("intitle:CV" in q for q in requetes["queries_generales"])
    assert any("inurl:cv" in q for q in requetes["queries_generales"])
    assert any("site:linkedin.com/in" in q for q in requetes["queries_linkedin"])
    assert any("site:fr.linkedin.com/in" in q for q in requetes["queries_linkedin"])
    assert all('-"senior"' in q for q in requetes["queries_linkedin"])


def test_requetes_github_utilisent_operateurs_natifs():
    profil = {
        "hard_skills": ["Angular", "TypeScript"],
        "niveau_experience": "senior",
        "type_contrat": "indifferent",
        "localisations": ["Montpellier"],
        "remote": False,
    }

    requetes = _enrichir_requetes(
        {
            "queries_generales": [],
            "queries_linkedin": [],
            "queries_github": [],
            "queries_cv_sites": [],
            "tags_stackoverflow": [],
        },
        profil,
    )

    github_query = " ".join(requetes["queries_github"])
    assert "location:Montpellier" in github_query
    assert "language:TypeScript" in github_query
    assert "followers:>5" in github_query


def _vide():
    return {"queries_generales": [], "queries_linkedin": [], "queries_github": [],
            "queries_cv_sites": [], "tags_stackoverflow": []}


def test_requetes_confirme_sans_termes_obligatoires_d_experience():
    profil = {"hard_skills": ["Python", "FastAPI"], "niveau_experience": "confirme",
              "localisations": ["Montpellier"], "remote": True}
    requetes = _enrichir_requetes(
        {**_vide(), "queries_linkedin": ['site:linkedin.com/in "Python" "Montpellier"']}, profil
    )
    toutes = " ".join(q for k, v in requetes.items() if k != "tags_stackoverflow" for q in v)
    assert '"3 ans"' not in toutes and "environs" not in toutes and "télétravail" not in toutes
    # le lieu n'est pas ajouté une seconde fois
    assert all(q.count("Montpellier") == 1 for q in requetes["queries_linkedin"])


def test_requetes_github_sans_mots_libres_ajoutes():
    profil = {"hard_skills": ["Python"], "niveau_experience": "confirme",
              "localisations": ["Montpellier"], "remote": True}
    requetes = _enrichir_requetes({**_vide(), "queries_github": ['"python" fastapi']}, profil)
    assert requetes["queries_github"][0] == "python fastapi location:Montpellier"
    assert all("confirm" not in q and "ans" not in q.split() for q in requetes["queries_github"])
