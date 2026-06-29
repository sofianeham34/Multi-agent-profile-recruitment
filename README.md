# Automated Recruitment Multi-Agent System

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic%20AI-orange)
![FastAPI](https://img.shields.io/badge/FastAPI-REST%20API-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Microservices-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-97%20automated-brightgreen)

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
- **97 tests automatisés** (pytest)

---

## Architecture

```
Fiche de poste
      │
      ▼
[A1] Orchestrateur
      │
      ▼
[A2] Analyste ──── Profil de compétences
      │
      ▼
[A3a] Chercheur Stratège ──── Requêtes ciblées
      │
      ▼
[A3b] Chercheur Collecteur ──── GitHub · DuckDuckGo · Stack Overflow
      │
      ▼
[A3c] Chercheur Filtre
      │
      ▼
[A6] Déduplicateur
      │
      ├──── [A4] Évaluateur  ┐
      ├──── [A4] Évaluateur  ├── fan-out parallèle (1 par candidat)
      └──── [A4] Évaluateur  ┘
                │
      ┌─────────┘
      ├──── [A5] Vérificateur ┐
      └──── [A5] Vérificateur ┘
                │
         ── validation humaine ──
                │
                ▼
      [A7] Recruteur ──── Messages personnalisés
                │
                ▼
      [A8] Persistance ──── ChromaDB (RAG)
                │
                ▼
          Rapport final
```

---

## Stack technique

| Couche | Technologies |
|---|---|
| Orchestration agents | LangGraph, LangChain |
| LLM | Groq API — LLaMA 3.3 70B / 3.1 8B |
| API REST | FastAPI |
| Mémoire vectorielle | ChromaDB (RAG) |
| Sources candidats | GitHub API, DuckDuckGo, Stack Overflow API |
| Interface utilisateur | Web UI (port 8080) |
| Déploiement | Docker Compose — monolithique ou microservices |
| Tests | pytest — 97 tests |

---

## Installation

### Prérequis
- Python 3.10+
- Clé API Groq gratuite → [console.groq.com](https://console.groq.com)

```bash
git clone https://github.com/<ton-compte>/TER_M1_Agentic_AI.git
cd TER_M1_Agentic_AI

cp .env.example .env
# Renseigner GROQ_API_KEY dans .env

python3 -m venv venv && source venv/bin/activate
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
tests/               # 97 tests automatisés
```
