# Automated Recruitment Multi-Agent System

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic%20AI-orange)
![FastAPI](https://img.shields.io/badge/FastAPI-REST%20API-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Microservices-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-113%20automated-brightgreen)

Système multi-agents (SMA) basé sur l'IA agentique pour automatiser la recherche et le contact de candidats développeurs. À partir d'une simple fiche de poste, le pipeline identifie, évalue et contacte les profils pertinents — avec validation humaine avant tout envoi.

> TER Master 1 Informatique — Université de Montpellier  
> Auteurs : Sofiane Hammar, Anouar Soufyani, Souad Hadji  
> Encadrant : M. Abdelhak-Djamel Seriai

---

## Fonctionnalités

- **Pipeline entièrement automatisé** : de la fiche de poste au rapport final
- **8 agents spécialisés** orchestrés par LangGraph avec parallélisation fan-out
- **Multi-sources** : GitHub, DuckDuckGo, Stack Overflow
- **RAG (mémoire vectorielle)** : historique des candidats via ChromaDB
- **Human-in-the-loop** : validation humaine avant contact des candidats
- **Double déploiement** : mode local LangGraph ou microservices Docker
- **113 tests automatisés** (pytest), dont une exécution complète du pipeline sans réseau

---

## Architecture

Graphe LangGraph tel que construit dans [`src/graph.py`](src/graph.py) :

```mermaid
flowchart TD
    F([Fiche de poste]) --> A1[A1 Orchestrateur]
    A1 --> A2[A2 Analyste<br/>profil de compétences]
    A2 --> A3a[A3a Stratège<br/>requêtes par source]
    A3a --> A3b[A3b Collecteur<br/>GitHub · DuckDuckGo · Stack Overflow]
    A3b --> A3c[A3c Filtre<br/>anti-bruit + scraping]
    A3c --> A6[A6 Déduplicateur]
    A6 -->|"Send() × N"| A4[A4 Évaluateur<br/>1 instance par candidat]
    A4 --> R4[Agrégation des scores]
    R4 -->|"Send() × N"| A5[A5 Vérificateur<br/>1 instance par candidat]
    A5 --> R5[Agrégation des validations]
    R5 --> RAG[Injection RAG<br/>candidats déjà connus]
    RAG -->|score suffisant| H{{Validation humaine}}
    H --> A7[A7 Recruteur<br/>messages personnalisés]
    RAG -->|aucun candidat viable| RP[Rapport final]
    A7 --> RP
    RP --> A8[(A8 Persistance<br/>ChromaDB)]
```

- **Parallélisme** : A4 et A5 sont lancés en une instance par candidat (pattern `Send` de LangGraph), avec un nombre d'appels LLM simultanés borné.
- **Blackboard** : tous les agents partagent un état typé (`GraphState`), chacun écrivant dans ses propres champs.
- **Human-in-the-loop** : le graphe s'interrompt avant A7 ; l'utilisateur approuve, modifie ou rejette la liste des candidats à contacter.
- **Mémoire inter-runs** : les candidats évalués sont stockés dans ChromaDB et réutilisés pour des fiches de poste similaires (calibration, cache de scores, liste noire).

## Interface

| Interface web (NiceGUI) | API REST (FastAPI / Swagger) |
|---|---|
| ![Interface](docs/screenshots/interface.jpg) | ![API](docs/screenshots/api-docs.jpg) |

---

## Stack technique

| Couche | Technologies |
|---|---|
| Orchestration agents | LangGraph, LangChain |
| LLM | Groq API — gpt-oss-120b / gpt-oss-20b |
| API REST | FastAPI |
| Mémoire vectorielle | ChromaDB (RAG) |
| Sources candidats | GitHub API, DuckDuckGo, Stack Overflow API |
| Interface utilisateur | Web UI (port 8080) |
| Déploiement | Docker Compose — monolithique ou microservices |
| Tests | pytest — 113 tests |

---

## Installation

### Prérequis
- Python 3.10+
- Clé API Groq → [console.groq.com](https://console.groq.com)

```bash
git clone https://github.com/sofianeham34/Multi-agent-profile-recruitment.git
cd Multi-agent-profile-recruitment

cp .env.example .env
# Renseigner GROQ_API_KEY dans .env

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run_local.sh
```

| Service | URL |
|---|---|
| Interface web | http://localhost:8080 |
| API REST | http://localhost:8000 |

### Docker (microservices)
```bash
docker compose -f docker-compose.microservices.yml up --build
```

---

## Utilisation CLI

```bash
# Fiche de poste en argument direct
python -m src.main "Développeur Python senior, 5 ans d'expérience, Paris"

# Depuis un fichier
python -m src.main --fichier fiche_poste.txt

# Sans validation humaine
python -m src.main --no-interrupt "Data Scientist ML, remote"
```

---

## Tests

```bash
python3 -m pytest tests/ -v
```

---

## Structure

```
src/
├── agents/          # 8 agents spécialisés
├── tools/           # GitHub API, DuckDuckGo, Stack Overflow, RAG
├── ui/              # Interface web
├── graph.py         # Graphe LangGraph
├── api.py           # API Gateway FastAPI
└── state.py         # État partagé entre agents
services/            # Microservices Docker (1 par agent)
tests/               # 113 tests automatisés
```
