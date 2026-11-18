#!/usr/bin/env bash
# L'API reste interne au conteneur ; seule l'interface est exposée,
# sur le port fourni par l'hébergeur (PORT) ou 8080 par défaut.
set -e
export UI_PORT="${PORT:-${UI_PORT:-8080}}"
uvicorn src.api:app --host 127.0.0.1 --port 8000 &
exec python -m src.ui.app
