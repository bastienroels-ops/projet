# Flambée — image de production
#
# L'image embarque ffmpeg, les polices et l'application. Les données (comptes,
# projets, rendus) vivent dans un volume : l'image reste jetable, l'état non.

FROM python:3.12-slim AS base

# ffmpeg fait tout le travail vidéo ; fontconfig permet à libass de trouver
# les polices embarquées ; tini assure la bonne propagation des signaux.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig tini curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FLAMBEE_WORK_DIR=/donnees/travail \
    FLAMBEE_OUTPUT_DIR=/donnees/rendus \
    FLAMBEE_ASSETS_DIR=/app/assets \
    FLAMBEE_HOST=0.0.0.0 \
    FLAMBEE_PORT=8000

WORKDIR /app

# Les dépendances d'abord : cette couche ne se reconstruit que si elles changent.
COPY requirements.txt requirements-optional.txt ./
RUN pip install -r requirements.txt

# Transcription (Script Viral, Voice Studio). Commente cette ligne pour une
# image plus légère, au prix de ces deux rubriques.
RUN pip install -r requirements-optional.txt

COPY flambee/ ./flambee/
COPY assets/ ./assets/

# L'application ne tourne pas en root.
RUN useradd --create-home --uid 10001 flambee \
    && mkdir -p /donnees/travail /donnees/rendus \
    && chown -R flambee:flambee /app /donnees
USER flambee

VOLUME ["/donnees"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "flambee.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
