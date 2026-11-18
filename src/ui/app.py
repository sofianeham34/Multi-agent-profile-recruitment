"""
Interface graphique NiceGUI pour le SMA de recrutement.

L'UI reste volontairement cliente de l'API FastAPI : elle lance les runs via
REST, suit l'exécution via SSE et soumet les décisions HITL sans importer le
graphe LangGraph.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime
from typing import Any

import httpx
from nicegui import app, background_tasks, ui

from src.ui.api_client import (
    API_URL,
    cancel_run,
    clear_rag,
    clear_runs,
    delete_run,
    get_metrics_summary,
    get_rag_summary,
    get_rapport,
    health,
    launch_run,
    list_runs,
    search_rag,
    stream_events,
    submit_hitl,
)
from src.ui.layout import badge_statut, page_frame


NODE_LABELS = {
    "orchestrateur": "A1 Orchestrateur",
    "analyste": "A2 Analyste",
    "chercheur_stratege": "A3a Stratège",
    "chercheur_collecteur": "A3b Collecteur",
    "chercheur_filtre": "A3c Filtre",
    "deduplicateur": "A6 Déduplicateur",
    "evaluateur": "A4 Évaluateur",
    "reduce_scores": "Regroupement des scores",
    "verificateur": "A5 Vérificateur",
    "reduce_validations": "Regroupement des vérifications",
    "injection_rag": "Mémoire (candidats déjà connus)",
    "recruteur": "A7 Recruteur",
    "rapport": "Rapport",
    "persistance": "A8 RAG",
}

PIPELINE_ORDER = [
    "orchestrateur",
    "analyste",
    "chercheur_stratege",
    "chercheur_collecteur",
    "chercheur_filtre",
    "deduplicateur",
    "evaluateur",
    "reduce_scores",
    "verificateur",
    "recruteur",
    "rapport",
    "persistance",
]


def _install_theme() -> None:
    ui.add_head_html(
        """
        <style>
        :root {
          --ink: #172018;
          --moss: #355e3b;
          --leaf: #5f7f52;
          --sand: #f4ecd8;
          --paper: rgba(255, 252, 241, 0.86);
          --line: rgba(42, 64, 43, 0.16);
        }
        body {
          color: var(--ink);
          background:
            radial-gradient(circle at 14% 10%, rgba(95, 127, 82, 0.26), transparent 30%),
            radial-gradient(circle at 88% 18%, rgba(197, 135, 66, 0.20), transparent 28%),
            linear-gradient(135deg, #f7f1df 0%, #e8ead4 48%, #dce5cf 100%);
          font-family: "Aptos", "Segoe UI", sans-serif;
        }
        .glass-card {
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: 24px;
          box-shadow: 0 22px 70px rgba(34, 54, 35, 0.12);
          backdrop-filter: blur(14px);
        }
        .section-title {
          letter-spacing: -0.04em;
          line-height: 0.95;
        }
        .timeline-item {
          border-left: 4px solid var(--leaf);
          background: rgba(255, 255, 255, 0.72);
        }
        .metric-card {
          background: linear-gradient(145deg, rgba(255,255,255,.76), rgba(231,239,219,.86));
          border: 1px solid var(--line);
          border-radius: 20px;
        }
        .report-markdown table {
          display: block;
          width: 100%;
          overflow-x: auto;
          border-collapse: collapse;
          font-size: 0.92rem;
        }
        .report-markdown th,
        .report-markdown td {
          border: 1px solid rgba(42, 64, 43, 0.16);
          padding: 8px 10px;
          vertical-align: top;
        }
        .report-markdown th {
          background: rgba(53, 94, 59, 0.10);
          color: var(--ink);
        }
        .report-markdown pre {
          white-space: pre-wrap;
          border-radius: 12px;
        }
        </style>
        """
    )
    ui.colors(primary="#355e3b", secondary="#c58742", accent="#5f7f52")


def _format_date(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return value


def _candidate_rows(candidats: list[dict] | None) -> list[dict]:
    rows = []
    for i, c in enumerate(candidats or []):
        rows.append({
            "id": c.get("candidat_id") or str(i),
            "url": c.get("url") or "",
            "nom": c.get("nom", "?"),
            "score_final": c.get("score_final", c.get("score", "")),
            "statut": c.get("statut", ""),
            "source": c.get("source", ""),
            "remarques": c.get("remarques", "")[:160],
        })
    return rows


def _candidate_columns() -> list[dict]:
    return [
        {"name": "nom", "label": "Candidat", "field": "nom", "align": "left"},
        {"name": "score_final", "label": "Score", "field": "score_final", "align": "left"},
        {"name": "statut", "label": "Statut", "field": "statut", "align": "left"},
        {"name": "source", "label": "Source", "field": "source", "align": "left"},
        {"name": "remarques", "label": "Remarques", "field": "remarques", "align": "left"},
    ]


def _candidate_table(candidats: list[dict] | None):
    """Tableau des candidats ; le nom renvoie vers le profil quand l'URL est connue."""
    table = ui.table(columns=_candidate_columns(), rows=_candidate_rows(candidats), row_key="id").classes("w-full")
    table.add_slot(
        "body-cell-nom",
        '<q-td :props="props"><a v-if="props.row.url" :href="props.row.url" target="_blank" rel="noopener">'
        "{{ props.value }}</a><span v-else>{{ props.value }}</span></q-td>",
    )
    return table


def _candidats_csv(candidats: list[dict] | None) -> str:
    """Export CSV des candidats (séparateur ; pour Excel en français)."""
    import csv
    import io

    sortie = io.StringIO()
    ecrivain = csv.writer(sortie, delimiter=";")
    ecrivain.writerow(["Nom", "Score", "Statut", "Source", "Lien", "Remarques"])
    for c in sorted(candidats or [], key=lambda x: x.get("score_final", 0), reverse=True):
        ecrivain.writerow([c.get("nom", ""), c.get("score_final", ""), c.get("statut", ""),
                           c.get("source", ""), c.get("url") or "", c.get("remarques", "")])
    return "\ufeff" + sortie.getvalue()  # BOM : accents corrects dans Excel


def _candidate_stats(candidats: list[dict] | None) -> dict[str, int]:
    stats = {"valide": 0, "douteux": 0, "invalide": 0}
    for candidat in candidats or []:
        statut = str(candidat.get("statut", "")).lower()
        if statut in stats:
            stats[statut] += 1
    stats["total"] = sum(stats.values())
    return stats


def _scores_stats(candidats: list[dict] | None) -> dict[str, float]:
    scores = []
    for candidat in candidats or []:
        try:
            scores.append(float(candidat.get("score_final", candidat.get("score", 0)) or 0))
        except (TypeError, ValueError):
            continue
    if not scores:
        return {"max": 0.0, "moyenne": 0.0}
    return {"max": max(scores), "moyenne": round(sum(scores) / len(scores), 1)}


def _status_chart_options(stats: dict[str, int]) -> dict:
    data = [
        {"value": stats.get("valide", 0), "name": "Valides", "itemStyle": {"color": "#2f855a"}},
        {"value": stats.get("douteux", 0), "name": "Douteux", "itemStyle": {"color": "#c58742"}},
        {"value": stats.get("invalide", 0), "name": "Invalides", "itemStyle": {"color": "#c2410c"}},
    ]
    if not any(item["value"] for item in data):
        data = [{"value": 1, "name": "Aucun candidat", "itemStyle": {"color": "#9ca3af"}}]
    return {
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"bottom": 0, "left": "center"},
        "series": [{
            "name": "Statuts",
            "type": "pie",
            "radius": ["44%", "72%"],
            "center": ["50%", "42%"],
            "avoidLabelOverlap": True,
            "label": {"formatter": "{b}\n{c}", "fontSize": 12},
            "data": data,
        }],
    }


def _extract_report_section(markdown: str, title: str) -> str:
    """Extrait le contenu d'une section Markdown de niveau 2."""
    if not markdown:
        return ""
    pattern = rf"^## {re.escape(title)}\s*$"
    match = re.search(pattern, markdown, flags=re.MULTILINE)
    if not match:
        return ""
    start = match.end()
    next_section = re.search(r"^##\s+", markdown[start:], flags=re.MULTILINE)
    end = start + next_section.start() if next_section else len(markdown)
    return markdown[start:end].strip()


def _strip_text_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:text)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_execution_metrics(markdown: str) -> dict[str, Any]:
    """Transforme la section métriques du rapport en données affichables."""
    section = _strip_text_fence(_extract_report_section(markdown, "Métriques d'exécution"))
    run_match = re.search(r"Run ID\s*:\s*(.+)", section)
    duration_match = re.search(r"Durée totale\s*:\s*([\d.]+)s", section)

    steps: list[dict[str, Any]] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("Run ID", "Durée totale", "Détail par étape")):
            continue
        match = re.match(r"^(?P<etape>\S+)\s+(?P<duree>[\d.]+|n/a)s\s*(?P<details>.*)$", line)
        if not match:
            continue
        duree_raw = match.group("duree")
        duree = None if duree_raw == "n/a" else float(duree_raw)
        steps.append({
            "etape": match.group("etape"),
            "duree_s": duree,
            "duree_label": "n/a" if duree is None else f"{duree:.2f}s",
            "details": match.group("details").strip() or "-",
        })

    slowest = max((s for s in steps if s["duree_s"] is not None), key=lambda s: s["duree_s"], default=None)
    return {
        "run_id": run_match.group(1).strip() if run_match else "-",
        "duree_totale_s": float(duration_match.group(1)) if duration_match else 0.0,
        "steps": steps,
        "slowest": slowest,
        "raw": section,
    }


def _metrics_chart_options(metrics: dict[str, Any]) -> dict:
    steps = [s for s in metrics.get("steps", []) if s.get("duree_s") is not None]
    top_steps = sorted(steps, key=lambda s: s["duree_s"], reverse=True)[:10]
    top_steps.reverse()
    if not top_steps:
        return {}
    return {
        "grid": {"left": 130, "right": 30, "top": 20, "bottom": 30},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "xAxis": {"type": "value", "name": "secondes"},
        "yAxis": {"type": "category", "data": [s["etape"] for s in top_steps]},
        "series": [{
            "type": "bar",
            "data": [s["duree_s"] for s in top_steps],
            "itemStyle": {"color": "#355e3b"},
            "label": {"show": True, "position": "right", "formatter": "{c}s"},
        }],
    }


def _build_mermaid(completed: set[str], awaiting_hitl: bool = False) -> str:
    lines = [
        "flowchart LR",
        "  orchestrateur[A1 Orchestrateur] --> analyste[A2 Analyste]",
        "  analyste --> chercheur_stratege[A3a Stratege]",
        "  chercheur_stratege --> chercheur_collecteur[A3b Collecteur]",
        "  chercheur_collecteur --> chercheur_filtre[A3c Filtre]",
        "  chercheur_filtre --> deduplicateur[A6 Deduplicateur]",
        "  deduplicateur --> evaluateur[A4 Evaluateur x N]",
        "  evaluateur --> reduce_scores[Reduce]",
        "  reduce_scores --> verificateur[A5 Verificateur]",
        "  verificateur --> recruteur[A7 Recruteur]",
        "  verificateur --> rapport[Rapport]",
        "  recruteur --> rapport",
        "  rapport --> persistance[A8 RAG]",
        "  classDef pending fill:#fffaf0,stroke:#b7a77a,color:#172018;",
        "  classDef done fill:#dcefd5,stroke:#355e3b,color:#172018;",
        "  classDef wait fill:#ffe1b8,stroke:#c58742,color:#172018;",
    ]
    for node in PIPELINE_ORDER:
        lines.append(f"  class {node} {'done' if node in completed else 'pending'};")
    if awaiting_hitl:
        lines.append("  class recruteur wait;")
    return "\n".join(lines)


def _event_title(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "node_completed":
        return f"{NODE_LABELS.get(data.get('node'), data.get('node', 'Nœud'))} terminé"
    labels = {
        "run_started": "Recherche démarrée",
        "awaiting_hitl": "Validation humaine requise",
        "hitl_decision": "Décision humaine reçue",
        "run_done": "Recherche terminée",
        "run_error": "Recherche arrêtée",
        "stream_closed": "Fin du suivi en direct",
        "connection_error": "Connexion au suivi perdue",
    }
    return labels.get(event_type, event_type)


def _message_erreur(exc: Exception) -> str:
    """Message lisible : le « detail » renvoyé par l'API plutôt que le code HTTP brut."""
    reponse = getattr(exc, "response", None)
    if reponse is not None:
        try:
            detail = reponse.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, str) and detail:
            return detail
    return str(exc)


def _mes_recherches() -> list[str]:
    """Recherches lancées depuis ce navigateur (stockage NiceGUI propre à chaque visiteur)."""
    return app.storage.user.setdefault("recherches", [])


async def _etat_demo() -> dict:
    """Mode démo publique annoncé par l'API (vide si l'API ne répond pas)."""
    try:
        return (await health()).get("demo") or {}
    except Exception:  # noqa: BLE001
        return {}


@ui.page("/")
async def home_page() -> None:
    _install_theme()
    with page_frame("Accueil"):
        with ui.card().classes("glass-card w-full p-8 gap-5"):
            ui.label("Trouver des candidats avec des agents IA").classes(
                "section-title text-5xl font-black text-primary"
            )
            ui.label(
                "Collez une fiche de poste : les agents cherchent des profils et les évaluent. Vous choisissez "
                "les candidats à retenir, puis les agents rédigent leurs messages de contact (rien n'est envoyé)."
            ).classes("text-lg text-grey-8")

            api_status = ui.label("API : vérification...").classes("text-sm text-grey-7")
            demo: dict = {}
            try:
                status = await health()
                demo = status.get("demo") or {}
                llm = status.get("llm") or {}
                api_status.text = (
                    f"API connectée · {status.get('runs_running', 0)} recherche(s) en cours"
                    + (f" · LLM : {llm.get('modele')} via {llm.get('fournisseur')}" if llm else "")
                )
            except Exception as exc:  # noqa: BLE001
                api_status.text = f"API indisponible ({API_URL}) : {exc}"
                api_status.classes(add="text-negative")

            if demo.get("active"):
                ui.label(
                    f"Démo publique : {demo.get('restantes', 0)} recherche(s) restante(s) aujourd'hui sur "
                    f"{demo.get('max_par_jour')}, une à la fois. Une recherche dure 3 à 5 minutes ; "
                    "vos recherches restent consultables dans l'historique de ce navigateur."
                ).classes("text-sm text-orange-9")

            fiche = ui.textarea(
                label="Fiche de poste",
                placeholder="Ex : Développeur backend Python, 2 à 4 ans d'expérience, Lyon, Django, PostgreSQL...",
            ).props("outlined autogrow").classes("w-full")
            if demo.get("max_fiche"):
                fiche.props(f"maxlength={demo['max_fiche']} counter")
            fiche.value = (
                "Développeur backend Python, 2 à 4 ans d'expérience, CDI à Lyon. "
                "Compétences : Python, Django, PostgreSQL, Docker, Git. Esprit d'équipe."
            )
            exemples = {
                "Backend Python": "Développeur backend Python, 2 à 4 ans d'expérience, CDI à Lyon. Compétences : Python, Django, PostgreSQL, Docker, Git. Esprit d'équipe.",
                "Front-end React": "Développeur front-end React et TypeScript, 2 ans d'expérience, Bordeaux. Compétences : React, TypeScript, CSS, tests.",
                "Data engineer": "Data engineer confirmé, 3 ans d'expérience, Paris. Compétences : Python, SQL, Spark, Airflow.",
            }
            with ui.row().classes("items-center gap-2"):
                ui.label("Exemples :").classes("text-sm text-grey-7")
                for nom, texte in exemples.items():
                    ui.chip(nom, on_click=lambda t=texte: fiche.set_value(t)).props("outline clickable")
            hitl = ui.checkbox(
                "Valider moi-même les candidats avant la rédaction des messages",
                value=True,
            )

            async def start_run() -> None:
                if not fiche.value or not fiche.value.strip():
                    ui.notify("La fiche de poste est vide.", type="warning")
                    return
                try:
                    result = await launch_run(fiche.value.strip(), no_interrupt=not hitl.value)
                except httpx.HTTPError as exc:
                    ui.notify(_message_erreur(exc), type="negative", multi_line=True)
                    return
                _mes_recherches().append(result["run_id"])
                ui.navigate.to(f"/runs/{result['run_id']}")

            lancer = ui.button("Lancer la recherche", icon="rocket_launch", on_click=start_run).props(
                "unelevated size=lg"
            ).classes("self-start")
            if demo.get("active") and demo.get("restantes", 1) <= 0:
                lancer.disable()


@ui.page("/runs")
async def runs_page() -> None:
    _install_theme()
    with page_frame("Historique"):
        ui.label("Recherches").classes("section-title text-4xl font-black text-primary")
        lecture_seule = bool((await _etat_demo()).get("active"))
        container = ui.column().classes("w-full gap-3")

        async def confirmer(message: str, action) -> None:
            with ui.dialog() as dialog, ui.card().classes("p-5 gap-4"):
                ui.label(message)
                with ui.row().classes("justify-end w-full gap-2"):
                    ui.button("Annuler", on_click=dialog.close).props("flat")

                    async def valider() -> None:
                        dialog.close()
                        try:
                            await action()
                        except Exception as exc:  # noqa: BLE001
                            ui.notify(f"Suppression impossible : {_message_erreur(exc)}", type="negative")
                        await refresh()

                    ui.button("Supprimer", color="negative", on_click=valider).props("unelevated")
            dialog.open()

        async def refresh() -> None:
            container.clear()
            try:
                runs = await list_runs()
                if lecture_seule:
                    # Démo publique : chaque visiteur ne voit que ses propres recherches.
                    mes = set(_mes_recherches())
                    runs = [r for r in runs if r.get("run_id") in mes]
            except Exception as exc:  # noqa: BLE001
                with container:
                    ui.label(f"Impossible de charger les recherches : {exc}").classes("text-negative")
                return

            with container:
                if not runs:
                    ui.label(
                        "Aucune recherche lancée depuis ce navigateur pour le moment."
                        if lecture_seule else "Aucune recherche pour le moment."
                    ).classes("text-grey-7")
                    return
                for run in runs:
                    actif = run.get("status") in ("running", "awaiting_hitl")
                    with ui.card().classes("glass-card w-full p-4"):
                        with ui.row().classes("items-start justify-between w-full gap-3 no-wrap"):
                            with ui.column().classes("gap-1 min-w-0"):
                                # La fiche de poste complète : c'est elle qui identifie la recherche.
                                ui.label(run.get("fiche_poste") or run["run_id"]).classes(
                                    "font-medium text-sm whitespace-pre-wrap break-words"
                                )
                                ui.label(
                                    f"Début : {_format_date(run.get('started_at'))} · "
                                    f"Fin : {_format_date(run.get('finished_at'))}"
                                ).classes("text-xs text-grey-7")
                            with ui.row().classes("items-center gap-1 no-wrap"):
                                badge_statut(run.get("status", "?"))
                                ui.button(
                                    "Ouvrir",
                                    icon="open_in_new",
                                    on_click=lambda r=run: ui.navigate.to(f"/runs/{r['run_id']}"),
                                ).props("flat")
                                if lecture_seule:
                                    continue
                                bouton = ui.button(
                                    icon="delete",
                                    on_click=lambda r=run: confirmer(
                                        "Supprimer cette recherche de l'historique ?",
                                        lambda: delete_run(r["run_id"]),
                                    ),
                                ).props("flat round color=negative").tooltip(
                                    "Annulez la recherche avant de la supprimer" if actif else "Supprimer"
                                )
                                if actif:
                                    bouton.disable()

        await refresh()
        with ui.row().classes("gap-2"):
            ui.button("Rafraîchir", icon="refresh", on_click=refresh).props("outline")
            vider_historique = ui.button(
                "Vider l'historique",
                icon="delete_sweep",
                on_click=lambda: confirmer(
                    "Supprimer toutes les recherches terminées ? Les recherches en cours sont gardées.",
                    clear_runs,
                ),
            ).props("outline color=negative")
            vider_historique.set_visibility(not lecture_seule)


@ui.page("/runs/{run_id}")
async def run_detail_page(run_id: str) -> None:
    _install_theme()
    with page_frame("Recherche"):
        try:
            initial = await get_rapport(run_id)
        except Exception as exc:  # noqa: BLE001
            ui.label(f"Run introuvable ou API indisponible : {exc}").classes("text-negative")
            return

        completed: set[str] = set()
        awaiting_hitl = initial.get("status") == "awaiting_hitl"

        with ui.card().classes("glass-card w-full p-5"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.column().classes("gap-1"):
                    ui.label("Recherche").classes("section-title text-4xl font-black text-primary")
                    if initial.get("fiche_poste"):
                        ui.label(initial["fiche_poste"]).classes("text-base whitespace-pre-wrap break-words")
                    ui.label(run_id).classes("font-mono text-xs text-grey-6")
                with ui.row().classes("items-center gap-2"):
                    status_box = ui.row().classes("items-center gap-2")
                    with status_box:
                        badge_statut(initial.get("status", "?"))

                    async def annuler() -> None:
                        try:
                            await cancel_run(run_id)
                        except httpx.HTTPError as exc:
                            ui.notify(f"Annulation impossible : {_message_erreur(exc)}", type="negative")
                            return
                        ui.notify("Annulation demandée : la recherche s'arrête à la fin de l'étape en cours.")

                    bouton_annuler = ui.button("Annuler", icon="stop", on_click=annuler).props(
                        "outline color=negative"
                    )
                    bouton_annuler.set_visibility(initial.get("status") in ("running", "awaiting_hitl"))

            diagram = ui.mermaid(_build_mermaid(completed, awaiting_hitl)).classes("w-full")

        hitl_box = ui.column().classes("w-full gap-3")
        result_box = ui.column().classes("w-full gap-3")
        with ui.card().classes("glass-card w-full p-5 gap-2"):
            ui.label("Chronologie").classes("text-2xl font-bold text-primary")
            timeline = ui.column().classes("w-full gap-1")
            with timeline:
                ui.label("Connexion au suivi en direct...").classes("text-grey-7")

        def update_status(status: str) -> None:
            status_box.clear()
            with status_box:
                badge_statut(status)
            bouton_annuler.set_visibility(status in ("running", "awaiting_hitl"))

        def add_event(event_type: str, data: dict[str, Any]) -> None:
            if event_type == "node_completed" and data.get("node") == "__interrupt__":
                return  # pause interne de LangGraph : l'événement « Validation humaine requise » suit
            # Une ligne par étape ; le détail technique reste replié.
            heure = str(data.get("ts", ""))[11:19]
            details = {k: v for k, v in data.items() if k != "ts"}
            with timeline:
                if event_type == "run_error" and data.get("erreur"):
                    ui.label(f"{heure}  {_event_title(event_type, data)} : {data['erreur']}").classes("text-negative")
                elif details:
                    with ui.expansion(f"{heure}  {_event_title(event_type, data)}").classes("w-full").props("dense"):
                        ui.markdown(f"```json\n{json.dumps(details, ensure_ascii=False, indent=2)}\n```").classes(
                            "text-xs w-full"
                        )
                else:
                    ui.label(f"{heure}  {_event_title(event_type, data)}").classes("text-sm px-4 py-1")

        async def render_report() -> None:
            result_box.clear()
            try:
                rapport = await get_rapport(run_id)
            except Exception as exc:  # noqa: BLE001
                with result_box:
                    ui.label(f"Rapport non chargeable : {exc}").classes("text-negative")
                return

            update_status(rapport.get("status", "?"))
            if rapport.get("status") in ("running", "awaiting_hitl"):
                return  # la synthèse s'affiche une fois la recherche terminée
            with result_box:
                with ui.card().classes("glass-card w-full p-5"):
                    ui.label("Synthèse").classes("text-2xl font-bold text-primary")
                    if rapport.get("erreur"):
                        couleur = "text-grey-7" if rapport.get("status") == "interrupted" else "text-negative"
                        ui.label(rapport["erreur"]).classes(couleur)
                    if rapport.get("candidats"):
                        _candidate_table(rapport.get("candidats"))
                    if rapport.get("rapport"):
                        ui.button(
                            "Ouvrir le rapport complet",
                            icon="article",
                            on_click=lambda: ui.navigate.to(f"/rapport/{run_id}"),
                        ).props("unelevated")

        def render_hitl(candidats: list[dict]) -> None:
            hitl_box.clear()
            with hitl_box:
                with ui.card().classes("glass-card w-full p-5 gap-3"):
                    ui.label("Validation humaine avant tout contact").classes(
                        "text-2xl font-bold text-secondary"
                    )
                    ui.label(
                        "Cochez les candidats à retenir : un message de contact sera rédigé pour chacun. "
                        "Rien n'est envoyé automatiquement, vous copiez les messages depuis le rapport."
                    ).classes("text-grey-8")
                    choix: dict[str, Any] = {}
                    proposables = [c for c in candidats if c.get("statut") in ("valide", "douteux")]
                    masques = len(candidats) - len(proposables)
                    if not proposables:
                        ui.label("Aucun candidat valide ou douteux à proposer.").classes("text-grey-7")
                    elif not any(c.get("statut") == "valide" for c in proposables):
                        ui.label(
                            "Aucun profil n'a été validé automatiquement : ouvrez les profils douteux "
                            "et cochez ceux qui vous conviennent."
                        ).classes("text-orange-9")
                    for c in proposables:
                        with ui.row().classes("items-center gap-3 w-full no-wrap"):
                            choix[c.get("candidat_id", "")] = ui.checkbox(value=c.get("statut") == "valide")
                            with ui.column().classes("gap-0 min-w-0"):
                                with ui.row().classes("items-center gap-2"):
                                    if c.get("url"):
                                        ui.link(c.get("nom", "?"), c["url"], new_tab=True).classes("font-medium")
                                    else:
                                        ui.label(c.get("nom", "?")).classes("font-medium")
                                    ui.badge(f"{c.get('score_final', 0):.0f}/100", color="primary")
                                    ui.badge(c.get("statut", "?"), color={"valide": "green", "douteux": "orange"}.get(c.get("statut"), "grey"))
                                ui.label(c.get("remarques", "")[:220]).classes("text-xs text-grey-7")

                    if masques:
                        ui.label(f"{masques} profil(s) invalide(s) masqué(s) : ils ne seront pas contactés.").classes(
                            "text-xs text-grey-6"
                        )

                    async def decide(action: str, payload: list | None = None) -> None:
                        try:
                            await submit_hitl(run_id, action, payload)
                        except httpx.HTTPError as exc:
                            ui.notify(f"Décision refusée par l'API : {_message_erreur(exc)}", type="negative")
                            return
                        # Notification avant de vider la zone : le bouton cliqué en fait partie.
                        ui.notify("Décision envoyée, reprise du pipeline.", type="positive")
                        update_status("running")
                        hitl_box.clear()

                    async def contacter_selection() -> None:
                        retenus = [
                            {**c, "statut": "valide"}
                            for c in candidats
                            if choix.get(c.get("candidat_id", "")) and choix[c.get("candidat_id", "")].value
                        ]
                        if not retenus:
                            await decide("skip")
                            return
                        await decide("edit", retenus)

                    async def tout_rejeter() -> None:
                        await decide("skip")

                    with ui.row().classes("gap-2"):
                        ui.button("Rédiger les messages", icon="edit_note", on_click=contacter_selection).props("unelevated")
                        ui.button("Ne retenir personne", icon="block", on_click=tout_rejeter).props(
                            "outline color=negative"
                        )

        async def consume_stream() -> None:
            first = True
            async for event in stream_events(run_id):
                if first:
                    timeline.clear()
                    first = False

                event_type = event.get("type", "unknown")
                data = event.get("data", {})
                add_event(event_type, data)

                if event_type == "node_completed":
                    completed.add(data.get("node", ""))
                    diagram.content = _build_mermaid(completed, awaiting_hitl=False)
                    diagram.update()
                elif event_type == "awaiting_hitl":
                    update_status("awaiting_hitl")
                    render_hitl(data.get("candidats", []))
                    diagram.content = _build_mermaid(completed, awaiting_hitl=True)
                    diagram.update()
                elif event_type == "hitl_decision":
                    update_status("running")
                    diagram.content = _build_mermaid(completed, awaiting_hitl=False)
                    diagram.update()
                elif event_type in {"run_done", "run_error", "stream_closed", "connection_error"}:
                    await render_report()

        background_tasks.create(consume_stream())
        await render_report()


@ui.page("/rapport/{run_id}")
async def rapport_page(run_id: str) -> None:
    _install_theme()
    with page_frame("Rapport"):
        try:
            rapport = await get_rapport(run_id)
        except Exception as exc:  # noqa: BLE001
            ui.label(f"Impossible de charger le rapport : {exc}").classes("text-negative")
            return

        rows = _candidate_rows(rapport.get("candidats"))
        stats = _candidate_stats(rapport.get("candidats"))
        score_stats = _scores_stats(rapport.get("candidats"))
        report_markdown = rapport.get("rapport") or ""
        recherche_section = _extract_report_section(report_markdown, "Recherches effectuées")
        execution_metrics = _parse_execution_metrics(report_markdown)

        with ui.card().classes("glass-card w-full p-5 gap-4"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("Rapport final").classes("section-title text-4xl font-black text-primary")
                badge_statut(rapport.get("status", "?"))
            if rapport.get("fiche_poste"):
                ui.label(rapport["fiche_poste"]).classes("text-base whitespace-pre-wrap break-words")
            with ui.row().classes("items-center gap-3 text-sm text-grey-7"):
                ui.label(f"Démarré : {_format_date(rapport.get('started_at'))}")
                ui.label(f"Terminé : {_format_date(rapport.get('finished_at'))}")
            with ui.row().classes("gap-2"):
                if report_markdown:
                    ui.button(
                        "Rapport (.md)",
                        icon="download",
                        on_click=lambda: ui.download.content(report_markdown, f"rapport-{run_id[:8]}.md"),
                    ).props("outline")
                if rapport.get("candidats"):
                    ui.button(
                        "Candidats (.csv)",
                        icon="table_view",
                        on_click=lambda: ui.download.content(
                            _candidats_csv(rapport.get("candidats")), f"candidats-{run_id[:8]}.csv", "text/csv"
                        ),
                    ).props("outline")

        with ui.row().classes("w-full gap-3"):
            for label, value, tone in [
                ("Total candidats", stats["total"], "text-primary"),
                ("Valides", stats["valide"], "text-green-8"),
                ("Douteux", stats["douteux"], "text-orange-8"),
                ("Invalides", stats["invalide"], "text-red-8"),
                ("Score max", f"{score_stats['max']:.1f}", "text-primary"),
                ("Score moyen", f"{score_stats['moyenne']:.1f}", "text-primary"),
            ]:
                with ui.card().classes("metric-card p-4 grow min-w-32"):
                    ui.label(str(value)).classes(f"text-3xl font-black {tone}")
                    ui.label(label).classes("text-sm text-grey-7")

        with ui.card().classes("glass-card w-full p-5 gap-4"):
            ui.label("Répartition des statuts").classes("text-2xl font-bold text-primary")
            ui.echart(_status_chart_options(stats)).classes("w-full h-80")

        messages = rapport.get("messages") or []
        urls = {c.get("candidat_id"): c.get("url") for c in rapport.get("candidats") or []}

        with ui.tabs().classes("w-full") as tabs:
            tab_candidats = ui.tab("Candidats", icon="groups")
            tab_messages = ui.tab(f"Messages ({len(messages)})", icon="mail")
            tab_recherches = ui.tab("Recherches", icon="manage_search")
            tab_metriques = ui.tab("Métriques", icon="bar_chart")
            tab_rapport = ui.tab("Rapport complet", icon="article")

        with ui.tab_panels(tabs, value=tab_candidats).classes("w-full bg-transparent"):
            with ui.tab_panel(tab_candidats).classes("p-0"):
                with ui.card().classes("glass-card w-full p-5 gap-4"):
                    ui.label("Candidats du run").classes("text-2xl font-bold text-primary")
                    if rows:
                        _candidate_table(rapport.get("candidats"))
                    else:
                        ui.label("Aucun candidat retourné pour cette recherche.").classes("text-grey-7")

            with ui.tab_panel(tab_messages).classes("p-0"):
                with ui.card().classes("glass-card w-full p-5 gap-4"):
                    ui.label("Messages de contact").classes("text-2xl font-bold text-primary")
                    if not messages:
                        ui.label("Aucun message rédigé pour cette recherche.").classes("text-grey-7")
                    for msg in messages:
                        texte = f"Objet : {msg.get('objet', '')}\n\n{msg.get('message', '')}"
                        with ui.card().classes("w-full p-4 gap-2"):
                            with ui.row().classes("items-center justify-between w-full"):
                                with ui.row().classes("items-center gap-2"):
                                    url = urls.get(msg.get("candidat_id"))
                                    if url:
                                        ui.link(msg.get("nom", "Candidat"), url, new_tab=True).classes("font-bold")
                                    else:
                                        ui.label(msg.get("nom", "Candidat")).classes("font-bold")
                                    ui.badge(msg.get("canal", "?"), color="secondary")
                                ui.button(
                                    "Copier",
                                    icon="content_copy",
                                    on_click=lambda t=texte: (ui.clipboard.write(t), ui.notify("Message copié")),
                                ).props("flat")
                            ui.label(f"Objet : {msg.get('objet', '')}").classes("font-medium")
                            ui.label(msg.get("message", "")).classes("whitespace-pre-wrap text-sm")

            with ui.tab_panel(tab_recherches).classes("p-0"):
                with ui.card().classes("glass-card w-full p-5 gap-4"):
                    ui.label("Recherches lancées").classes("text-2xl font-bold text-primary")
                    if recherche_section:
                        ui.markdown(recherche_section).classes("report-markdown w-full")
                    else:
                        ui.label("Aucune recherche détaillée disponible pour ce run.").classes("text-grey-7")

            with ui.tab_panel(tab_metriques).classes("p-0"):
                with ui.card().classes("glass-card w-full p-5 gap-4"):
                    ui.label("Métriques d'exécution").classes("text-2xl font-bold text-primary")
                    with ui.row().classes("w-full gap-3"):
                        for label, value in [
                            ("Run métriques", execution_metrics["run_id"]),
                            ("Durée totale", f"{execution_metrics['duree_totale_s']:.1f}s"),
                            ("Étapes mesurées", len(execution_metrics["steps"])),
                            (
                                "Étape la plus lente",
                                execution_metrics["slowest"]["etape"] if execution_metrics["slowest"] else "-",
                            ),
                        ]:
                            with ui.card().classes("metric-card p-4 grow min-w-32"):
                                ui.label(str(value)).classes("text-2xl font-black text-primary")
                                ui.label(label).classes("text-sm text-grey-7")

                    chart_options = _metrics_chart_options(execution_metrics)
                    if chart_options:
                        ui.label("Temps par étape").classes("text-lg font-bold text-primary")
                        ui.echart(chart_options).classes("w-full h-96")

                    if execution_metrics["steps"]:
                        ui.table(
                            columns=[
                                {"name": "etape", "label": "Étape", "field": "etape", "align": "left"},
                                {"name": "duree_label", "label": "Durée", "field": "duree_label", "align": "left"},
                                {"name": "details", "label": "Détails", "field": "details", "align": "left"},
                            ],
                            rows=execution_metrics["steps"],
                            row_key="etape",
                        ).classes("w-full")
                    elif execution_metrics["raw"]:
                        ui.markdown(f"```text\n{execution_metrics['raw']}\n```").classes("w-full")
                    else:
                        ui.label("Aucune métrique détaillée disponible pour ce run.").classes("text-grey-7")

            with ui.tab_panel(tab_rapport).classes("p-0"):
                with ui.card().classes("glass-card w-full p-5 gap-4"):
                    ui.label("Rapport Markdown").classes("text-2xl font-bold text-primary")
                    ui.markdown(
                        report_markdown or "Rapport pas encore disponible."
                    ).classes("report-markdown w-full")


@ui.page("/rag")
async def rag_page() -> None:
    _install_theme()
    with page_frame("Mémoire RAG"):
        ui.label("Mémoire vectorielle").classes("section-title text-4xl font-black text-primary")
        try:
            summary = await get_rag_summary()
        except Exception as exc:  # noqa: BLE001
            ui.label(f"Impossible de lire l'état RAG : {exc}").classes("text-negative")
            return

        with ui.row().classes("w-full gap-3"):
            for label, value in [
                ("Candidats mémorisés", summary.get("candidats", 0)),
                ("Fiches de poste", summary.get("fiches_poste", 0)),
            ]:
                with ui.card().classes("metric-card p-4 grow"):
                    ui.label(str(value)).classes("text-3xl font-black text-primary")
                    ui.label(label).classes("text-sm text-grey-7")
        if summary.get("error"):
            ui.label(summary["error"]).classes("text-negative")

        async def vider() -> None:
            with ui.dialog() as dialog, ui.card().classes("p-5 gap-4"):
                ui.label("Effacer toute la mémoire (candidats évalués et fiches de poste) ?")
                with ui.row().classes("justify-end w-full gap-2"):
                    ui.button("Annuler", on_click=dialog.close).props("flat")

                    async def valider() -> None:
                        dialog.close()
                        try:
                            await clear_rag()
                        except httpx.HTTPError as exc:
                            ui.notify(f"Impossible de vider la mémoire : {_message_erreur(exc)}", type="negative")
                            return
                        ui.navigate.reload()

                    ui.button("Effacer", color="negative", on_click=valider).props("unelevated")
            dialog.open()

        bouton_vider = ui.button("Vider la mémoire", icon="delete_sweep", on_click=vider).props("outline color=negative")
        bouton_vider.set_visibility(not (await _etat_demo()).get("active"))

        with ui.card().classes("glass-card w-full p-5 gap-3"):
            ui.label("Recherche de profils similaires").classes("text-2xl font-bold text-primary")
            query = ui.textarea(label="Profil ou mots-clés", placeholder="Python FastAPI RAG Docker...").props(
                "outlined autogrow"
            ).classes("w-full")
            fiche = ui.textarea(
                label="Fiche de poste de contexte (optionnel)",
                placeholder="Si renseignée, la recherche est calibrée sur les fiches similaires.",
            ).props("outlined autogrow").classes("w-full")
            results_box = ui.column().classes("w-full")

            async def run_search() -> None:
                if not query.value or not query.value.strip():
                    ui.notify("Saisissez un profil ou des mots-clés.", type="warning")
                    return
                results_box.clear()
                with results_box:
                    ui.spinner(size="lg")
                try:
                    payload = await search_rag(query.value.strip(), fiche.value.strip() if fiche.value else None)
                except Exception as exc:  # noqa: BLE001
                    results_box.clear()
                    with results_box:
                        ui.label(f"Recherche impossible : {exc}").classes("text-negative")
                    return
                results_box.clear()
                rows = payload.get("results", [])
                with results_box:
                    if rows:
                        ui.table(
                            columns=[
                                {"name": "nom", "label": "Nom", "field": "nom", "align": "left"},
                                {"name": "score", "label": "Score", "field": "score", "align": "left"},
                                {"name": "similarite", "label": "Similarité", "field": "similarite", "align": "left"},
                                {"name": "source", "label": "Source", "field": "source", "align": "left"},
                                {"name": "remarques", "label": "Remarques", "field": "remarques", "align": "left"},
                            ],
                            rows=rows,
                            row_key="nom",
                        ).classes("w-full")
                    else:
                        ui.label("Aucun profil similaire trouvé.").classes("text-grey-7")

            ui.button("Rechercher", icon="search", on_click=run_search).props("unelevated")
            with results_box:
                ui.label("La recherche charge le modèle d'embedding uniquement au clic.").classes(
                    "text-xs text-grey-7"
                )


@ui.page("/metriques")
async def metrics_page() -> None:
    _install_theme()
    with page_frame("Métriques"):
        ui.label("Observabilité").classes("section-title text-4xl font-black text-primary")
        try:
            payload = await get_metrics_summary()
        except Exception as exc:  # noqa: BLE001
            ui.label(f"Impossible de charger les métriques : {exc}").classes("text-negative")
            return

        current = payload.get("current", {})
        history = payload.get("history", [])

        with ui.row().classes("w-full gap-3"):
            with ui.card().classes("metric-card p-4 grow"):
                ui.label(str(current.get("run_id", "-"))).classes("text-xl font-black text-primary")
                ui.label("Dernière recherche").classes("text-sm text-grey-7")
            with ui.card().classes("metric-card p-4 grow"):
                ui.label(f"{current.get('duree_totale_s', 0)} s").classes("text-3xl font-black text-primary")
                ui.label("Durée en mémoire").classes("text-sm text-grey-7")
            with ui.card().classes("metric-card p-4 grow"):
                ui.label(str(len(current.get("etapes", {})))).classes("text-3xl font-black text-primary")
                ui.label("Étapes tracées").classes("text-sm text-grey-7")

        rows = []
        for name, data in (current.get("etapes") or {}).items():
            rows.append({
                "etape": name,
                "duree_s": data.get("duree_s", ""),
                "details": ", ".join(
                    f"{k}={v}" for k, v in data.items() if k not in {"debut", "fin", "duree_s"}
                )[:220],
            })
        with ui.card().classes("glass-card w-full p-5"):
            ui.label("Étapes de la dernière recherche").classes("text-2xl font-bold text-primary")
            if rows:
                ui.table(
                    columns=[
                        {"name": "etape", "label": "Étape", "field": "etape", "align": "left"},
                        {"name": "duree_s", "label": "Durée (s)", "field": "duree_s", "align": "left"},
                        {"name": "details", "label": "Details", "field": "details", "align": "left"},
                    ],
                    rows=rows,
                    row_key="etape",
                ).classes("w-full")
            else:
                ui.label("Aucune étape en mémoire. Lancez un run pour alimenter cette vue.").classes("text-grey-7")

        history_rows = [
            {
                "file": item.get("_file", ""),
                "run_id": item.get("run_id", ""),
                "duree_totale_s": item.get("duree_totale_s", ""),
                "etapes": len(item.get("etapes", {})),
            }
            for item in history
        ]
        with ui.card().classes("glass-card w-full p-5"):
            ui.label("Derniers exports logs/metriques_*.json").classes("text-2xl font-bold text-primary")
            if history_rows:
                ui.table(
                    columns=[
                        {"name": "file", "label": "Fichier", "field": "file", "align": "left"},
                        {"name": "run_id", "label": "Run", "field": "run_id", "align": "left"},
                        {
                            "name": "duree_totale_s",
                            "label": "Durée totale (s)",
                            "field": "duree_totale_s",
                            "align": "left",
                        },
                        {"name": "etapes", "label": "Etapes", "field": "etapes", "align": "left"},
                    ],
                    rows=history_rows,
                    row_key="file",
                ).classes("w-full")
            else:
                ui.label("Aucun export de métriques trouvé dans logs/.").classes("text-grey-7")


def main() -> None:
    ui.run(
        title="SMA Recrutement",
        host=os.getenv("UI_HOST", "0.0.0.0"),
        port=int(os.getenv("UI_PORT", "8080")),
        reload=os.getenv("UI_RELOAD", "false").lower() == "true",
        show=False,
        show_welcome_message=False,
        # Signe le cookie qui identifie le navigateur (historique propre à chaque visiteur).
        storage_secret=os.getenv("UI_STORAGE_SECRET") or secrets.token_urlsafe(32),
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
