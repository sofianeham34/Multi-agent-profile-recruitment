#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8080}"
API_URL="${API_URL:-http://localhost:${API_PORT}}"

# Environnement virtuel : .venv ou venv
VENV=""
for candidat in .venv venv; do
  if [ -x "${candidat}/bin/python" ] && [ -x "${candidat}/bin/uvicorn" ]; then
    VENV="${candidat}"
    break
  fi
done
if [ -z "${VENV}" ]; then
  echo "Erreur: environnement virtuel introuvable. Lance d'abord:"
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/pip install -r requirements.txt"
  exit 1
fi

mkdir -p logs data

api_pid=""
ui_pid=""

cleanup() {
  echo
  echo "Arrêt API/UI..."
  if [ -n "${ui_pid}" ] && kill -0 "${ui_pid}" 2>/dev/null; then
    kill "${ui_pid}" 2>/dev/null || true
  fi
  if [ -n "${api_pid}" ] && kill -0 "${api_pid}" 2>/dev/null; then
    kill "${api_pid}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}

trap cleanup INT TERM EXIT

echo "Démarrage API sur http://localhost:${API_PORT}"
"${VENV}/bin/uvicorn" src.api:app --reload --port "${API_PORT}" > logs/api_local.log 2>&1 &
api_pid="$!"

echo "Démarrage UI sur http://localhost:${UI_PORT}"
API_URL="${API_URL}" UI_PORT="${UI_PORT}" "${VENV}/bin/python" -m src.ui.app > logs/ui_local.log 2>&1 &
ui_pid="$!"

echo
echo "Projet lancé."
echo "UI  : http://localhost:${UI_PORT}"
echo "API : http://localhost:${API_PORT}"
echo
echo "Logs:"
echo "  tail -f logs/api_local.log"
echo "  tail -f logs/ui_local.log"
echo
echo "Appuie sur Ctrl+C pour arrêter les deux serveurs."

# S'arrête dès que l'un des deux serveurs s'arrête (compatible bash 3.2 de macOS)
while kill -0 "${api_pid}" 2>/dev/null && kill -0 "${ui_pid}" 2>/dev/null; do
  sleep 1
done
