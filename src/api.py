"""API Gateway REST — FastAPI. Supporte LangGraph et microservices HTTP."""

import asyncio
import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from src.config import LLM_MODEL, LLM_PROVIDER
from src import config as _cfg
from src.state import etat_courant
from src.graph import build_graph
from src.config import graph_config
from src.observabilite import reset_metrics, get_metrics

app = FastAPI(title="SMA Recrutement — API Gateway", version="1.1.0")

# runs en mémoire : {run_id: RunInfo}
_runs: dict[str, dict] = {}

_HITL_TIMEOUT = _cfg.HITL_TIMEOUT  # secondes avant abandon HITL
_LOGS_DIR = Path(os.getenv("METRICS_DIR", "logs"))
_CHROMA_DIR = Path(os.getenv("CHROMADB_DIR", "data/chromadb"))
_RUNS_DB_PATH = Path(os.getenv("RUNS_DB_PATH", "data/runs.sqlite"))
_METRICS_HISTORY_LIMIT = 20
_DB_LOCK = threading.Lock()
_SVC_ORCHESTRATEUR = os.getenv("SVC_ORCHESTRATEUR", "").rstrip("/")


class RecruterRequest(BaseModel):
    fiche_poste: str
    no_interrupt: bool = True   # HITL désactivé par défaut via API


class RecruterResponse(BaseModel):
    run_id: str
    status: str
    message: str


class RapportResponse(BaseModel):
    run_id: str
    status: str
    fiche_poste: Optional[str] = None
    rapport: Optional[str] = None
    candidats: Optional[list] = None
    messages: Optional[list] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    erreur: Optional[str] = None


class HitlDecision(BaseModel):
    action: str                              # "approve" | "skip" | "edit"
    candidats_retenus: Optional[list] = None


def _push_event(run_id: str, event_type: str, data: dict) -> None:
    """Pousse un événement dans la file SSE du run."""
    info = _runs.get(run_id)
    if info is None:
        return
    q = info.get("_event_queue")
    if q is None:
        return
    q.put({
        "type": event_type,
        "data": {**data, "ts": datetime.now(timezone.utc).isoformat()},
    })


def _close_event_stream(run_id: str) -> None:
    """Sentinel None pour terminer le flux SSE."""
    info = _runs.get(run_id)
    if info is None:
        return
    q = info.get("_event_queue")
    if q is not None:
        q.put(None)


def _internal_run_fields(info: dict) -> dict:
    """Champs internes recréés au démarrage (non persistés)."""
    return {
        "_event_queue": queue.Queue(),
        "_hitl_event": threading.Event(),
        "_hitl_decision": {},
        "_candidats_pending_hitl": [],
        "_with_interrupt": info.get("_with_interrupt", False),
        "_cancel_event": threading.Event(),
    }


def _init_runs_db() -> None:
    """Crée la table SQLite si elle n'existe pas."""
    _RUNS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK, sqlite3.connect(_RUNS_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                fiche_poste TEXT NOT NULL,
                status TEXT NOT NULL,
                rapport TEXT,
                candidats_json TEXT,
                erreur TEXT,
                started_at TEXT,
                finished_at TEXT,
                with_interrupt INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Bases créées avant l'enregistrement des messages de contact.
        colonnes = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "messages_json" not in colonnes:
            conn.execute("ALTER TABLE runs ADD COLUMN messages_json TEXT")


def _save_run(run_id: str) -> None:
    """Persiste un run en SQLite."""
    info = _runs.get(run_id)
    if info is None:
        return
    with _DB_LOCK, sqlite3.connect(_RUNS_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, fiche_poste, status, rapport, candidats_json,
                erreur, started_at, finished_at, with_interrupt, messages_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                fiche_poste=excluded.fiche_poste,
                status=excluded.status,
                rapport=excluded.rapport,
                candidats_json=excluded.candidats_json,
                erreur=excluded.erreur,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                with_interrupt=excluded.with_interrupt,
                messages_json=excluded.messages_json
            """,
            (
                run_id,
                info.get("fiche_poste", ""),
                info.get("status", "running"),
                info.get("rapport"),
                json.dumps(info.get("candidats"), ensure_ascii=False),
                info.get("erreur"),
                info.get("started_at"),
                info.get("finished_at"),
                1 if info.get("_with_interrupt") else 0,
                json.dumps(info.get("messages"), ensure_ascii=False),
            ),
        )


def _delete_runs_db(run_ids: list[str]) -> None:
    """Supprime des runs de l'historique SQLite."""
    if not run_ids:
        return
    with _DB_LOCK, sqlite3.connect(_RUNS_DB_PATH) as conn:
        conn.executemany("DELETE FROM runs WHERE run_id = ?", [(rid,) for rid in run_ids])


def _load_runs_from_db() -> None:
    """Charge les runs SQLite au démarrage."""
    _init_runs_db()
    with _DB_LOCK, sqlite3.connect(_RUNS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()

    for row in rows:
        try:
            candidats = json.loads(row["candidats_json"]) if row["candidats_json"] else None
        except json.JSONDecodeError:
            candidats = None
        try:
            messages = json.loads(row["messages_json"]) if row["messages_json"] else None
        except json.JSONDecodeError:
            messages = None
        status = row["status"]
        erreur = row["erreur"]
        finished_at = row["finished_at"]
        # Anciennes versions : une recherche coupée était enregistrée comme une erreur.
        ancien_message = erreur == "Run interrompu par redémarrage de l'API."
        if status in ("running", "awaiting_hitl") or (status == "error" and ancien_message):
            # L'API s'est arrêtée pendant la recherche : ce n'est pas une erreur du pipeline.
            status = "interrupted"
            erreur = "Recherche interrompue : l'API a été arrêtée pendant l'exécution."
            finished_at = finished_at or datetime.now(timezone.utc).isoformat()

        info = {
            "run_id": row["run_id"],
            "fiche_poste": row["fiche_poste"],
            "status": status,
            "rapport": row["rapport"],
            "candidats": candidats,
            "messages": messages,
            "erreur": erreur,
            "started_at": row["started_at"],
            "finished_at": finished_at,
            "_with_interrupt": bool(row["with_interrupt"]),
        }
        info.update(_internal_run_fields(info))
        _runs[row["run_id"]] = info
        if status != row["status"] or erreur != row["erreur"]:
            _save_run(row["run_id"])


def _run_pipeline(run_id: str, fiche_poste: str, with_interrupt: bool) -> None:
    """Exécute le pipeline LangGraph dans un thread dédié."""
    from src.logger import get_logger
    log = get_logger("api")

    try:
        log.info("Run %s : démarrage du pipeline.", run_id)
        _runs[run_id]["status"] = "running"
        _save_run(run_id)
        _push_event(run_id, "run_started", {"fiche_poste": fiche_poste[:200]})

        reset_metrics()

        app_graph = build_graph(with_interrupt=with_interrupt)
        config = graph_config(run_id)

        cancel_event: threading.Event = _runs[run_id]["_cancel_event"]

        # première phase : jusqu'à interrupt ou fin
        for event in app_graph.stream(
            {"fiche_poste": fiche_poste},
            config=config,
            stream_mode="updates",
        ):
            if cancel_event.is_set():
                raise InterruptedError("Run annulé par l'utilisateur.")
            for node_name, payload in event.items():
                log.info("Run %s : nœud [%s] terminé.", run_id, node_name)
                _push_event(run_id, "node_completed", {
                    "node": node_name,
                    "summary": _resume_payload(payload),
                })

        # détection interrupt HITL
        state = app_graph.get_state(config)
        if with_interrupt and state.next:
            candidats_en_attente = etat_courant(state.values.get("candidats_valides", []))
            log.info(
                "Run %s : interrupt détecté avant %s, %d candidat(s) en attente.",
                run_id, state.next, len(candidats_en_attente),
            )
            _runs[run_id]["status"] = "awaiting_hitl"
            _runs[run_id]["_candidats_pending_hitl"] = candidats_en_attente
            _save_run(run_id)
            _push_event(run_id, "awaiting_hitl", {
                "candidats": candidats_en_attente,
                "next_node": state.next[0] if state.next else None,
            })

            # attente décision humaine
            hitl_event: threading.Event = _runs[run_id]["_hitl_event"]
            got_decision = hitl_event.wait(timeout=_HITL_TIMEOUT)
            if not got_decision:
                raise TimeoutError(
                    f"Aucune décision HITL reçue en {_HITL_TIMEOUT}s, run abandonné."
                )

            decision = _runs[run_id]["_hitl_decision"]
            log.info("Run %s : décision HITL reçue : %s", run_id, decision["action"])
            _push_event(run_id, "hitl_decision", {"action": decision["action"]})

            # application de la décision HITL
            if decision["action"] == "skip":
                # skip → invalide tous les candidats
                modifies = [
                    {**c, "statut": "invalide", "remarques": "Rejeté par HITL"}
                    for c in candidats_en_attente
                ]
                app_graph.update_state(config, {"candidats_valides": modifies})
            elif decision["action"] == "edit":
                # La liste du graphe fonctionne en ajout : les candidats non retenus
                # sont ajoutés en « invalide » pour que cette décision l'emporte.
                # Marqués « choix_humain » : A7 rédige pour eux tous, sans seuil de score ni top-N.
                retenus = [
                    {**c, "statut": "valide", "choix_humain": True}
                    for c in decision.get("candidats_retenus") or []
                ]
                ids_retenus = {c.get("candidat_id") for c in retenus}
                non_retenus = [
                    {**c, "statut": "invalide", "remarques": "Non retenu par la validation humaine"}
                    for c in candidats_en_attente
                    if c.get("candidat_id") not in ids_retenus
                ]
                app_graph.update_state(config, {"candidats_valides": non_retenus + retenus})
            # approve → reprise telle quelle

            _runs[run_id]["status"] = "running"
            _save_run(run_id)

            # reprise post-HITL
            for event in app_graph.stream(None, config=config, stream_mode="updates"):
                if cancel_event.is_set():
                    raise InterruptedError("Run annulé par l'utilisateur.")
                for node_name, payload in event.items():
                    log.info("Run %s : nœud [%s] terminé (post-HITL).", run_id, node_name)
                    _push_event(run_id, "node_completed", {
                        "node": node_name,
                        "summary": _resume_payload(payload),
                    })

        # état final
        final_state = app_graph.get_state(config)
        rapport = final_state.values.get("rapport_final", "")
        candidats = etat_courant(final_state.values.get("candidats_valides", []))
        messages_envoyes = final_state.values.get("messages_envoyes", [])

        _runs[run_id].update({
            "status": "done",
            "rapport": rapport,
            "candidats": candidats,
            "messages": messages_envoyes,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_run(run_id)
        _push_event(run_id, "run_done", {
            "rapport_chars": len(rapport),
            "candidats": len(candidats),
        })
        log.info("Run %s : pipeline terminé avec succès.", run_id)

    except Exception as exc:
        from src.logger import get_logger
        from src.config import describe_llm_error
        get_logger("api").exception("Run %s : erreur pipeline.", run_id)
        message = describe_llm_error(exc)
        _runs[run_id].update({
            "status": "error",
            "erreur": message,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_run(run_id)
        _push_event(run_id, "run_error", {"erreur": message})
    finally:
        _close_event_stream(run_id)


def _rapport_microservices(fiche_poste: str, payload: dict) -> str:
    """Construit le rapport Markdown depuis la réponse de l'orchestrateur."""
    valides = payload.get("candidats_valides", [])
    douteux = payload.get("candidats_douteux", [])
    invalides = payload.get("candidats_invalides", [])
    messages = payload.get("messages_envoyes", [])
    requetes = payload.get("requetes_recherche", {})

    def table(candidats: list[dict]) -> str:
        if not candidats:
            return "Aucun."
        rows = [
            "| Nom | Score | Statut | Source | Remarques |",
            "| --- | ---: | --- | --- | --- |",
        ]
        for c in sorted(candidats, key=lambda x: x.get("score_final", 0), reverse=True):
            rows.append(
                "| "
                + " | ".join([
                    str(c.get("nom", "?")).replace("|", "/"),
                    f"{float(c.get('score_final', 0)):.1f}",
                    str(c.get("statut", "")).replace("|", "/"),
                    str(c.get("source", "")).replace("|", "/"),
                    str(c.get("remarques", "")).replace("\n", " ").replace("|", "/"),
                ])
                + " |"
            )
        return "\n".join(rows)

    messages_section = "Aucun message généré."
    if messages:
        messages_section = "\n".join(
            f"- {m.get('nom', 'Candidat')} ({m.get('canal', 'canal non précisé')}) : {m.get('objet', 'Sans objet')}"
            for m in messages
        )

    labels = {
        "queries_generales": "Web général / CV / portfolios",
        "queries_linkedin": "LinkedIn",
        "queries_github": "GitHub Users API",
        "queries_cv_sites": "Sites CV / freelances",
        "tags_stackoverflow": "Tags Stack Overflow",
    }
    recherches_lignes = [
        "| Source | N° | Requête |",
        "| --- | ---: | --- |",
    ]
    for key, label in labels.items():
        valeurs = requetes.get(key, []) if isinstance(requetes, dict) else []
        for index, valeur in enumerate(valeurs, 1):
            safe_valeur = str(valeur).replace("|", "/")
            recherches_lignes.append(f"| {label} | {index} | `{safe_valeur}` |")
    recherches_section = (
        "\n".join(recherches_lignes)
        if len(recherches_lignes) > 2
        else "Aucune requête enregistrée."
    )

    return f"""# Rapport final de recrutement

## Mode d'exécution
- Architecture : microservices HTTP
- Orchestrateur : service `svc-orchestrateur`

## Résumé du poste
- Fiche de poste : {fiche_poste}

## Statistiques de recherche
- Résultats bruts collectés : {payload.get('resultats_bruts_count', 0)}
- Profils trouvés : {payload.get('profils_trouves', 0)}
- Profils après déduplication : {payload.get('profils_deduplication', 0)}
- Candidats évalués : {len(payload.get('candidats_scores', []))}
- Candidats valides : {len(valides)}
- Candidats douteux : {len(douteux)}
- Profils invalides / non-candidats : {len(invalides)}
- Messages générés : {len(messages)}

## Recherches effectuées
{recherches_section}

## Candidats valides à contacter
{table(valides)}

## Candidats douteux à vérifier manuellement
{table(douteux)}

## Profils invalides ou non-candidats
{table(invalides)}

## Messages générés
{messages_section}
"""


def _run_pipeline_microservices(run_id: str, fiche_poste: str, with_interrupt: bool) -> None:
    """Exécute le pipeline via l'orchestrateur HTTP, avec polling statut."""
    from src.logger import get_logger

    log = get_logger("api.microservices")
    try:
        _runs[run_id]["status"] = "running"
        _runs[run_id]["mode_execution"] = "microservices"
        _save_run(run_id)
        _push_event(run_id, "run_started", {
            "fiche_poste": fiche_poste[:200],
            "mode_execution": "microservices",
        })

        cancel_event: threading.Event = _runs[run_id]["_cancel_event"]
        hitl_event: threading.Event   = _runs[run_id]["_hitl_event"]

        # démarrage non-bloquant
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{_SVC_ORCHESTRATEUR}/pipeline",
                json={
                    "run_id": run_id,
                    "fiche_poste": fiche_poste,
                    "with_interrupt": with_interrupt,
                },
            )
            r.raise_for_status()

        # polling jusqu'à done ou error
        payload: dict | None = None
        while payload is None:
            if cancel_event.is_set():
                raise InterruptedError("Run annulé par l'utilisateur.")

            with httpx.Client(timeout=60.0) as client:
                r = client.get(f"{_SVC_ORCHESTRATEUR}/pipeline/{run_id}/status")
                r.raise_for_status()
                status_data = r.json()

            orch_status = status_data.get("status")

            if orch_status == "awaiting_hitl" and _runs[run_id]["status"] != "awaiting_hitl":
                # pause HITL détectée
                candidats = status_data.get("candidats_pending", [])
                _runs[run_id]["status"] = "awaiting_hitl"
                _runs[run_id]["_candidats_pending_hitl"] = candidats
                _save_run(run_id)
                _push_event(run_id, "awaiting_hitl", {
                    "candidats": candidats,
                    "next_node": "recruteur",
                })
                log.info(
                    "Run %s : pause HITL — %d candidat(s) en attente.", run_id, len(candidats)
                )

                # attente décision humaine
                got = hitl_event.wait(timeout=_HITL_TIMEOUT)
                if not got:
                    raise TimeoutError(
                        f"Aucune décision HITL reçue en {_HITL_TIMEOUT}s, run abandonné."
                    )
                if cancel_event.is_set():
                    raise InterruptedError("Run annulé par l'utilisateur.")

                decision = _runs[run_id]["_hitl_decision"]
                log.info("Run %s : décision HITL reçue : %s", run_id, decision["action"])
                _push_event(run_id, "hitl_decision", {"action": decision["action"]})

                # transmission décision à l'orchestrateur
                with httpx.Client(timeout=10.0) as client:
                    r = client.post(
                        f"{_SVC_ORCHESTRATEUR}/hitl/{run_id}",
                        json=decision,
                    )
                    r.raise_for_status()

                _runs[run_id]["status"] = "running"
                _save_run(run_id)

            elif orch_status == "done":
                payload = status_data.get("result") or {}

            elif orch_status == "error":
                raise RuntimeError(
                    status_data.get("error") or "Erreur inconnue dans l'orchestrateur."
                )

            else:
                time.sleep(2)  # attente poll suivant

        # traitement résultat final
        for node in (
            "analyste",
            "chercheur_stratege",
            "chercheur_collecteur",
            "chercheur_filtre",
            "deduplicateur",
            "evaluateur",
            "verificateur",
            "recruteur",
            "rapport",
        ):
            _push_event(run_id, "node_completed", {
                "node": node,
                "summary": {"mode": "microservices"},
            })

        candidats = (
            payload.get("candidats_valides", [])
            + payload.get("candidats_douteux", [])
            + payload.get("candidats_invalides", [])
        )
        rapport = payload.get("rapport") or _rapport_microservices(fiche_poste, payload)

        _runs[run_id].update({
            "status": "done",
            "rapport": rapport,
            "candidats": candidats,
            "messages": payload.get("messages_envoyes", []),
            "mode_execution": "microservices",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_run(run_id)
        _push_event(run_id, "run_done", {
            "rapport_chars": len(rapport),
            "candidats": len(candidats),
            "mode_execution": "microservices",
        })
        log.info("Run %s : pipeline microservices terminé avec succès.", run_id)

    except Exception as exc:  # noqa: BLE001
        log.exception("Run %s : erreur pipeline microservices.", run_id)
        _runs[run_id].update({
            "status": "error",
            "erreur": str(exc),
            "mode_execution": "microservices",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_run(run_id)
        _push_event(run_id, "run_error", {"erreur": str(exc)})
    finally:
        _close_event_stream(run_id)


def _run_pipeline_entry(run_id: str, fiche_poste: str, with_interrupt: bool) -> None:
    """Choisit le runtime : LangGraph ou microservices."""
    if _SVC_ORCHESTRATEUR:
        _run_pipeline_microservices(run_id, fiche_poste, with_interrupt)
    else:
        _run_pipeline(run_id, fiche_poste, with_interrupt)


def _resume_payload(payload) -> dict:
    """Résumé court d'un payload pour l'émission SSE."""
    if not isinstance(payload, dict):
        return {}
    resume = {}
    for key, value in payload.items():
        if isinstance(value, list):
            resume[key] = f"list({len(value)})"
        elif isinstance(value, str):
            resume[key] = value[:80]
        elif isinstance(value, dict):
            resume[key] = f"dict({len(value)})"
        else:
            resume[key] = str(value)[:80]
    return resume


def _metrics_snapshot() -> dict:
    """Snapshot des métriques en mémoire."""
    metrics = get_metrics()
    started = getattr(metrics, "_debut_run", datetime.now().timestamp())
    return {
        "run_id": getattr(metrics, "_run_id", None),
        "duree_totale_s": round(datetime.now().timestamp() - started, 2),
        "etapes": getattr(metrics, "_etapes", {}),
    }


def _load_metrics_history(limit: int = _METRICS_HISTORY_LIMIT) -> list[dict]:
    """Charge les derniers exports JSON depuis logs/."""
    if not _LOGS_DIR.exists():
        return []

    history: list[dict] = []
    files = sorted(_LOGS_DIR.glob("metriques_*.json"), reverse=True)[:limit]
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        payload["_file"] = path.name
        history.append(payload)
    return history


def _rag_counts() -> dict:
    """Compte les documents ChromaDB sans charger le modèle d'embedding."""
    if not _CHROMA_DIR.exists():
        return {
            "available": False,
            "persist_dir": str(_CHROMA_DIR),
            "candidats": 0,
            "fiches_poste": 0,
            "error": None,
        }

    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        candidats = client.get_or_create_collection("candidats_evalues").count()
        fiches = client.get_or_create_collection("fiches_poste").count()
        return {
            "available": True,
            "persist_dir": str(_CHROMA_DIR),
            "candidats": candidats,
            "fiches_poste": fiches,
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "persist_dir": str(_CHROMA_DIR),
            "candidats": 0,
            "fiches_poste": 0,
            "error": str(exc),
        }


_load_runs_from_db()


_RUNS_ACTIFS = ("running", "awaiting_hitl")


def _runs_du_jour() -> int:
    """Recherches lancées aujourd'hui (UTC), pour le plafond de la démo publique."""
    aujourdhui = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for r in _runs.values() if (r.get("started_at") or "").startswith(aujourdhui))


def _etat_demo() -> dict:
    if not _cfg.DEMO_PUBLIQUE:
        return {"active": False}
    return {
        "active": True,
        "max_par_jour": _cfg.DEMO_MAX_RUNS_JOUR,
        "restantes": max(0, _cfg.DEMO_MAX_RUNS_JOUR - _runs_du_jour()),
        "max_fiche": _cfg.DEMO_MAX_FICHE,
    }


def _refuser_en_demo() -> None:
    if _cfg.DEMO_PUBLIQUE:
        raise HTTPException(status_code=403, detail="Action désactivée sur la démo publique.")


@app.get("/health", tags=["Monitoring"])
async def health() -> JSONResponse:
    runs_total = len(_runs)
    runs_running = sum(
        1 for r in _runs.values() if r["status"] in ("running", "awaiting_hitl")
    )
    return JSONResponse({
        "status": "ok",
        "mode_execution": "microservices" if _SVC_ORCHESTRATEUR else "langgraph",
        "runs_total": runs_total,
        "runs_running": runs_running,
        "llm": {"fournisseur": LLM_PROVIDER, "modele": LLM_MODEL},
        "demo": _etat_demo(),
    })


@app.post("/recruter", response_model=RecruterResponse, tags=["Pipeline"])
async def recruter(
    req: RecruterRequest, background_tasks: BackgroundTasks
) -> RecruterResponse:
    """Lance un nouveau pipeline de recrutement."""
    if not req.fiche_poste.strip():
        raise HTTPException(status_code=422, detail="fiche_poste ne peut pas être vide.")
    if _cfg.DEMO_PUBLIQUE:
        if len(req.fiche_poste) > _cfg.DEMO_MAX_FICHE:
            raise HTTPException(
                status_code=422,
                detail=f"Fiche trop longue pour la démo ({_cfg.DEMO_MAX_FICHE} caractères maximum).",
            )
        if any(r["status"] in _RUNS_ACTIFS for r in _runs.values()):
            raise HTTPException(
                status_code=429,
                detail="Une recherche est déjà en cours sur la démo : réessayez dans quelques minutes.",
            )
        if _runs_du_jour() >= _cfg.DEMO_MAX_RUNS_JOUR:
            raise HTTPException(
                status_code=429,
                detail=f"Limite de la démo atteinte pour aujourd'hui ({_cfg.DEMO_MAX_RUNS_JOUR} recherches) : "
                "revenez demain ou consultez l'historique.",
            )

    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "run_id": run_id,
        "fiche_poste": req.fiche_poste,
        "status": "running",
        "rapport": None,
        "candidats": None,
        "erreur": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "mode_execution": "microservices" if _SVC_ORCHESTRATEUR else "langgraph",
        # champs internes SSE + HITL
        "_event_queue": queue.Queue(),
        "_hitl_event": threading.Event(),
        "_hitl_decision": {},
        "_candidats_pending_hitl": [],
        "_with_interrupt": not req.no_interrupt,
        "_cancel_event": threading.Event(),
    }
    _save_run(run_id)

    background_tasks.add_task(
        _run_pipeline_entry,
        run_id,
        req.fiche_poste,
        not req.no_interrupt,
    )

    return RecruterResponse(
        run_id=run_id,
        status="running",
        message=f"Pipeline lancé. Consultez /rapport/{run_id} pour le résultat.",
    )


@app.get("/rapport/{run_id}", response_model=RapportResponse, tags=["Pipeline"])
async def get_rapport(run_id: str) -> RapportResponse:
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' introuvable.")

    info = _runs[run_id]
    return RapportResponse(
        run_id=run_id,
        status=info["status"],
        fiche_poste=info.get("fiche_poste"),
        rapport=info.get("rapport"),
        candidats=info.get("candidats"),
        messages=info.get("messages"),
        started_at=info.get("started_at"),
        finished_at=info.get("finished_at"),
        erreur=info.get("erreur"),
    )


@app.get("/runs", tags=["Monitoring"])
async def list_runs() -> JSONResponse:
    summary = [
        {
            "run_id": r["run_id"],
            "status": r["status"],
            "fiche_poste": r.get("fiche_poste", ""),
            "mode_execution": r.get("mode_execution", "microservices" if _SVC_ORCHESTRATEUR else "langgraph"),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
        }
        for r in _runs.values()
    ]
    summary.sort(key=lambda x: x["started_at"] or "", reverse=True)
    return JSONResponse({"runs": summary, "total": len(summary)})


@app.get("/metrics", tags=["Monitoring"])
async def get_metrics_summary(
    limit: int = Query(default=_METRICS_HISTORY_LIMIT, ge=1, le=100),
) -> JSONResponse:
    return JSONResponse({
        "current": _metrics_snapshot(),
        "history": _load_metrics_history(limit),
    })


@app.get("/rag", tags=["Monitoring"])
async def get_rag_summary() -> JSONResponse:
    return JSONResponse(_rag_counts())


@app.delete("/rag", tags=["Monitoring"])
async def clear_rag() -> JSONResponse:
    """Vide la mémoire RAG (candidats évalués et fiches de poste)."""
    _refuser_en_demo()
    if any(r["status"] in _RUNS_ACTIFS for r in _runs.values()):
        raise HTTPException(status_code=409, detail="Une recherche est en cours : attendez sa fin avant de vider la mémoire.")
    try:
        from src.tools.rag import get_memoire

        get_memoire().vider()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(_rag_counts())


@app.get("/rag/search", tags=["Monitoring"])
async def search_rag(
    q: str = Query(..., min_length=1),
    fiche_poste: Optional[str] = Query(default=None),
    n: int = Query(default=5, ge=1, le=20),
) -> JSONResponse:
    try:
        from src.tools.rag import get_memoire

        results = get_memoire().rechercher_similaires(
            profil_brut=q,
            fiche_poste=fiche_poste,
            n_results=n,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return JSONResponse({"results": results, "total": len(results)})


@app.get("/runs/{run_id}/stream", tags=["Pipeline"])
async def stream_run(run_id: str) -> StreamingResponse:
    """Flux SSE des événements du pipeline."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' introuvable.")

    q: queue.Queue = _runs[run_id]["_event_queue"]

    async def event_generator():
        if _runs[run_id]["status"] in ("done", "error", "interrupted") and q.empty():
            yield "event: stream_closed\ndata: {}\n\n"
            return
        _HEARTBEAT_INTERVAL = 15.0  # heartbeat pour éviter timeout SSE
        last_heartbeat = asyncio.get_event_loop().time()
        while True:
            try:
                event = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.1)
                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
                continue
            if event is None:
                # sentinel de fin
                yield "event: stream_closed\ndata: {}\n\n"
                break
            yield (
                f"event: {event['type']}\n"
                f"data: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # désactive buffering nginx
        },
    )


@app.post("/runs/{run_id}/hitl", tags=["Pipeline"])
async def submit_hitl(run_id: str, decision: HitlDecision) -> JSONResponse:
    """Transmet une décision HITL (approve/skip/edit)."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' introuvable.")

    info = _runs[run_id]
    if info["status"] != "awaiting_hitl":
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' n'est pas en attente de décision (statut: {info['status']}).",
        )

    if decision.action not in ("approve", "skip", "edit"):
        raise HTTPException(
            status_code=422,
            detail="action doit être parmi : approve, skip, edit.",
        )

    if decision.action == "edit" and decision.candidats_retenus is None:
        raise HTTPException(
            status_code=422,
            detail="candidats_retenus est requis pour action=edit.",
        )

    info["_hitl_decision"] = {
        "action": decision.action,
        "candidats_retenus": decision.candidats_retenus or [],
    }
    info["_hitl_event"].set()
    _save_run(run_id)

    return JSONResponse({
        "run_id": run_id,
        "action": decision.action,
        "message": "Décision prise en compte, reprise du pipeline en cours.",
    })


@app.delete("/runs/{run_id}", tags=["Monitoring"])
async def delete_run(run_id: str) -> JSONResponse:
    """Supprime une recherche terminée de l'historique."""
    _refuser_en_demo()
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' introuvable.")
    if _runs[run_id]["status"] in _RUNS_ACTIFS:
        raise HTTPException(status_code=409, detail="Recherche en cours : annulez-la avant de la supprimer.")
    del _runs[run_id]
    _delete_runs_db([run_id])
    return JSONResponse({"run_id": run_id, "message": "Recherche supprimée."})


@app.delete("/runs", tags=["Monitoring"])
async def delete_finished_runs() -> JSONResponse:
    """Vide l'historique des recherches terminées (les recherches en cours sont gardées)."""
    _refuser_en_demo()
    termines = [rid for rid, r in _runs.items() if r["status"] not in _RUNS_ACTIFS]
    for rid in termines:
        del _runs[rid]
    _delete_runs_db(termines)
    return JSONResponse({"supprimes": len(termines)})


@app.post("/runs/{run_id}/cancel", tags=["Pipeline"])
async def cancel_run(run_id: str) -> JSONResponse:
    """Annule un run en cours. Le pipeline s'arrête proprement entre deux nœuds."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' introuvable.")

    info = _runs[run_id]
    if info["status"] not in ("running", "awaiting_hitl"):
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' ne peut pas être annulé (statut: {info['status']}).",
        )

    info["_cancel_event"].set()
    # débloquer aussi si en attente HITL
    if info["status"] == "awaiting_hitl":
        info["_hitl_event"].set()

    return JSONResponse({"run_id": run_id, "message": "Annulation demandée."})
