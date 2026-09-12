#!/usr/bin/env bash
# Lance Flambée en local sur http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ Création de l'environnement virtuel…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "⚠️  ffmpeg est introuvable. Installe-le : brew install ffmpeg (Mac) ou apt install ffmpeg (Linux)."
fi

PORT="${FLAMBEE_PORT:-8000}"
echo "→ Flambée sur http://127.0.0.1:${PORT}"
exec ./.venv/bin/python -m uvicorn flambee.app:app --host 127.0.0.1 --port "${PORT}"
