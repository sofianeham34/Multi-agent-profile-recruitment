"""Microservice A5 — Vérificateur."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from src.agents.verificateur import verificateur_node

app = FastAPI(title="A5 Verificateur", version="1.0")


class VerifRequest(BaseModel):
    candidat_score: dict
    profil_source: dict
    profil_competences: dict


@app.get("/health")
def health():
    return {"status": "ok", "service": "verificateur"}


@app.post("/verifier")
def verifier(body: VerifRequest):
    try:
        state = {
            "candidat_score": body.candidat_score,
            "profil_source": body.profil_source,
            "profil_competences": body.profil_competences,
            "candidats_valides": [],
        }
        result = verificateur_node(state)
        valides = result.get("candidats_valides", [])
        return {"candidat_valide": valides[0] if valides else None}
    except Exception as e:
        import traceback, logging
        logging.getLogger("verificateur").error("Erreur /verifier : %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
