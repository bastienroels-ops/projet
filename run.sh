#!/usr/bin/env bash
# Lance Flambée en local sur http://127.0.0.1:8000
#
#   ./run.sh                     accessible depuis cette machine uniquement
#   FLAMBEE_HOST=0.0.0.0 ./run.sh  accessible depuis le réseau local (téléphone)
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
HOST="${FLAMBEE_HOST:-127.0.0.1}"

if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  # Adresse à taper dans le navigateur du téléphone, sur le même Wi-Fi.
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null \
    || ipconfig getifaddr en1 2>/dev/null \
    || hostname -I 2>/dev/null | awk '{print $1}' \
    || echo '')"
  echo "→ Flambée sur http://${LAN_IP:-<ip-de-cette-machine>}:${PORT}"
  echo "   (ouvert au réseau local : toute personne sur ce Wi-Fi peut y accéder)"
else
  echo "→ Flambée sur http://127.0.0.1:${PORT}"
fi

exec ./.venv/bin/python -m uvicorn flambee.app:app --host "$HOST" --port "$PORT"
