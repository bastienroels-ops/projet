# 🔥 Flambée

Outil **100 % local et personnel** de montage vidéo vertical : à partir de 2 à 5 liens
TikTok / YouTube Shorts sur une même thématique, il produit une nouvelle vidéo 9:16
prête à publier (voix off IA + sous-titres animés + musique).

Pas de compte, pas de base de données, pas de déploiement : une petite webapp FastAPI
sur `http://127.0.0.1:8000`.

---

## Installation

```bash
# 1. ffmpeg (obligatoire)
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian / Ubuntu

# 2. le projet
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optionnel :

```bash
export ANTHROPIC_API_KEY="sk-ant-..."       # génération du script en un clic
pip install -r requirements-optional.txt    # faster-whisper (transcription locale)
```

## Lancement

```bash
./run.sh                 # crée le venv au besoin, puis démarre le serveur
# ou
uvicorn flambee.app:app --host 127.0.0.1 --port 8000
```

Puis ouvre <http://127.0.0.1:8000>. Le bandeau du haut indique si `ffmpeg`, `yt-dlp`
et la clé API sont détectés.

## Les 5 étapes

| Étape | Ce qui se passe |
|---|---|
| **1. Sources** | 2 à 5 liens collés → téléchargement `yt-dlp` dans `work/<projet>/sources`. Durée, résolution et nombre de vues sont affichés ; une vidéo de moins de 45 s déclenche un avertissement. |
| **2. Accroche** | Les 3 premières secondes de chaque source sont extraites (ffmpeg) et jouées côte à côte. Un clic choisit celle qui ouvrira le montage. |
| **3. Style** | Voix `edge-tts` (+ débit), sous-titres animés on/off, musique de fond et son volume, masquage (flou ou bandeau noir) des sous-titres incrustés dans les sources, fond d'ambiance des vidéos d'origine. |
| **4. Script** | Sujet + consignes → appel à l'API Claude, **ou** bouton « Copier le prompt » pour le coller dans une conversation Claude et rapporter le texte. Le script reste éditable, avec un compteur de mots et la durée estimée. |
| **5. Rendu** | Voix off → sous-titres `.ass` calés au mot → découpe et recadrage 9:16 des extraits → concaténation → mixage voix/musique → export `.mp4` dans `output/`. |

## Comment c'est monté

- La **durée de la vidéo suit la voix off** : le script est synthétisé d'abord, puis
  `trimmer.plan_segments()` répartit les extraits (2,5 à 6 s chacun) pour couvrir
  exactement cette durée, en alternant les sources et en évitant de repasser deux
  fois au même endroit.
- Les **sous-titres** utilisent les évènements `WordBoundary` d'edge-tts : pas de
  transcription, un calage au mot exact, rendu façon TikTok (ligne complète, mot
  actif surligné, léger « pop » en début de ligne).
- Chaque extrait est **normalisé** (1080×1920, 30 fps, H.264/AAC) avant la
  concaténation, qui se fait donc sans réencodage. Une seule passe finale incruste
  les sous-titres et mixe l'audio.

## Arborescence

```
flambee/
  config.py       réglages, formats, styles, prompt système
  media.py        helpers ffmpeg/ffprobe (probe, filtres 9:16, masque)
  downloader.py   étape 1 — wrapper yt-dlp
  trimmer.py      étape 2 — hooks + planification/découpe des extraits
  scriptgen.py    étape 4 — API Claude (+ mode manuel)
  voice.py        étape 5 — edge-tts et minutage mot à mot
  subtitles.py    étape 5 — génération du .ass animé
  assembler.py    étape 5 — concat, mixage, encodage final
  pipeline.py     orchestration des tâches de fond
  project.py      état d'un projet, persisté en JSON
  app.py          API FastAPI + page HTML
  templates/ static/
assets/music/     bibliothèque de musiques libres de droits (à remplir)
work/             fichiers de travail par projet
output/           vidéos finales
tests/            tests hors ligne (sources générées par ffmpeg)
```

## Musiques de fond

Dépose des fichiers `.mp3`/`.m4a`/`.wav` libres de droits dans `assets/music/` ;
ils apparaissent dans la liste de l'étape 3 au rechargement de la page. Sources
possibles : Pixabay Music, Free Music Archive, YouTube Audio Library.

## Réglages par variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `FLAMBEE_PORT` | `8000` | port du serveur local |
| `FLAMBEE_OUTPUT_DIR` | `./output` | dossier des rendus |
| `FLAMBEE_WORK_DIR` | `./work` | fichiers de travail |
| `FLAMBEE_MIN_DURATION` | `45` | seuil d'avertissement sur les sources |
| `FLAMBEE_HOOK_DURATION` | `3` | durée de l'accroche |
| `FLAMBEE_ANTHROPIC_MODEL` | `claude-sonnet-5` | modèle utilisé pour le script |
| `ANTHROPIC_API_KEY` | — | active la génération en un clic |
| `FLAMBEE_COOKIES_FROM_BROWSER` | — | `chrome`, `safari`, `firefox`… pour les vidéos qui exigent une connexion |
| `FLAMBEE_COOKIES_FILE` | — | fichier `cookies.txt` (format Netscape), alternative à l'option ci-dessus |

## Tests

```bash
pip install pytest httpx
pytest tests -q
```

Les tests fabriquent leurs propres vidéos avec ffmpeg : aucun téléchargement,
aucun appel réseau.

## Dépannage

- **« Outil manquant : ffmpeg »** → installe ffmpeg, ou pointe `FLAMBEE_FFMPEG`
  vers le binaire.
- **« Sign in to confirm you're not a bot »** → YouTube demande une session :
  lance Flambée avec `FLAMBEE_COOKIES_FROM_BROWSER=chrome ./run.sh` (le navigateur
  où tu es connecté), ou exporte un `cookies.txt` et pointe `FLAMBEE_COOKIES_FILE`
  dessus.
- **Téléchargement qui échoue** → `pip install -U yt-dlp` (les extracteurs
  changent souvent) ; certaines vidéos privées ou régionalisées restent inaccessibles.
- **Sous-titres dans une autre police** → `Arial Black` doit être installée ;
  sinon change `SubtitleStyle.font` dans `flambee/config.py`.
- **Rendu long** → c'est l'encodage final ; baisse la résolution dans
  `VideoFormat` ou passe `-preset` de `medium` à `veryfast` dans `assembler.py`.

## Usage

Outil personnel. Télécharger des vidéos TikTok/YouTube va à l'encontre des CGU de
ces plateformes : garde les sources en local, ne republie pas le contenu d'autrui
tel quel, ne redistribue pas l'outil. Vérifie aussi la licence des musiques que tu
ajoutes dans `assets/music/`.
