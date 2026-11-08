# Système multi-agents de recrutement automatisé

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic%20AI-orange)
![FastAPI](https://img.shields.io/badge/FastAPI-REST%20API-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Microservices-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-113%20automated-brightgreen)

**À partir d'une simple fiche de poste, 8 agents IA coopèrent pour trouver, évaluer et préparer le contact de développeurs — avec un humain qui garde la décision finale.**

Système multi-agents (SMA) construit avec LangGraph : un agent analyse le besoin, d'autres cherchent des profils
sur GitHub, Stack Overflow et le web, les évaluent en parallèle, se contrôlent mutuellement, puis rédigent des
messages de contact personnalisés. Les candidats évalués alimentent une mémoire vectorielle qui améliore les
recherches suivantes.

> TER Master 1 Informatique — Université de Montpellier  
> Auteurs : Sofiane Hammar, Anouar Soufyani, Souad Hadji  
> Encadrant : M. Abdelhak-Djamel Seriai

---

## Fonctionnalités

- **Pipeline de bout en bout** : de la fiche de poste au rapport final et aux messages de contact personnalisés
- **8 agents spécialisés** orchestrés par LangGraph, avec évaluation et vérification en parallèle
- **Multi-sources** : API GitHub, API Stack Overflow, recherche web (DuckDuckGo) et scraping
- **Mémoire RAG** (ChromaDB) : historique des candidats réutilisé d'un recrutement à l'autre
- **Human-in-the-loop** : par défaut, la liste des candidats à contacter est soumise à validation humaine
- **Interface web** avec suivi des agents en direct, **API REST** et **CLI**
- **Déploiement Docker** : monolithique ou un microservice par agent

## Défis techniques

| Problème | Solution mise en place |
|---|---|
| Les résultats de recherche contiennent surtout du bruit (offres d'emploi, articles, listes) | Agent filtre dédié (A3c) : élimination des URL non pertinentes, détection des pages agrégées, priorisation des profils individuels |
| Un LLM peut surévaluer un candidat | **Validation pair-à-pair** : A4 note, A5 contrôle indépendamment avec des règles métier (expérience, page non-candidat, adéquation minimale) |
| Trouver des candidats même sans profil « parfait » | **Routage absolu ou relatif** : contact au-dessus du seuil, sinon proposition des meilleurs profils viables |
| La mémoire ne doit pas biaiser un poste avec les scores d'un autre | **RAG calibré par fiche de poste** : les références ne sont utilisées que pour des fiches similaires (seuil de similarité), avec cache de scores et liste noire inter-runs |
| Les réponses d'un LLM ne sont pas toujours exploitables | Extraction JSON centralisée avec nouvelle tentative ; un candidat non évaluable est écarté, jamais noté au hasard |
| Les API externes et le LLM limitent le débit | Parallélisme borné (`max_concurrency`), relances exponentielles sur les erreurs 429 |

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

## Qualité

```bash
python3 -m pytest tests/ -v
```

- **113 tests automatisés** : règles de filtrage et de vérification, déduplication, génération des requêtes, mémoire RAG, API REST, sources externes
- **Test du pipeline complet sans réseau** : les 13 nœuds du graphe s'exécutent avec un LLM et des sources simulés (fan-out parallèle, routage, contact du seul bon candidat, rejet d'une réponse LLM inexploitable)
- **Observabilité** : durée et résultat de chaque agent exportés en JSON dans `logs/` à chaque run, consultables dans l'interface

## Recherche

Le dossier [`Etudes/`](Etudes) contient l'état de l'art réalisé pendant le TER : synthèses d'articles sur les
systèmes multi-agents et l'IA agentique, et comparatif des frameworks d'agents ayant mené au choix de LangGraph.

---

## Structure

```
src/
├── agents/          # 8 agents spécialisés
├── tools/           # GitHub API, DuckDuckGo, Stack Overflow, RAG
├── ui/              # Interface web
├── graph.py         # Graphe LangGraph
├── llm_json.py      # Appels LLM à réponse JSON (extraction, nouvelle tentative)
├── api.py           # API Gateway FastAPI
└── state.py         # État partagé entre agents
services/            # Microservices Docker (1 par agent)
tests/               # 113 tests automatisés
Etudes/              # État de l'art (SMA, IA agentique, frameworks)
```
