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

## Sans ordinateur : Flambée sur Google Colab

Le carnet [`colab/Flambee.ipynb`](colab/Flambee.ipynb) installe et démarre tout
sur une machine Google gratuite, et affiche une adresse HTTPS à ouvrir dans
Safari. Une seule cellule à lancer.

```
https://colab.research.google.com/github/bastienroels-ops/projet/blob/claude/fastapi-viral-video-montage-e9x7i8/colab/Flambee.ipynb
```

Si ce lien direct ne s'ouvre pas (le nom de branche contient une barre
oblique) : ouvrir [colab.research.google.com](https://colab.research.google.com)
→ *Ouvrir un notebook* → onglet **GitHub** → coller `bastienroels-ops/projet`
→ choisir `colab/Flambee.ipynb`.

Le carnet :

- récupère le code, installe les dépendances, démarre le serveur ;
- ouvre un tunnel HTTPS Cloudflare (gratuit, sans compte) et affiche l'adresse,
  l'identifiant et un mot de passe engendré ;
- affiche aussi le lien de secours propre à Colab, qui ne dépend d'aucun tunnel.

Limites à garder en tête :

- l'onglet Colab doit rester ouvert, c'est lui qui fait tourner le serveur ;
- **une erreur 530 ou 1033 dans Safari vient du tunnel, pas du rendu** :
  l'encodage sature la machine et la liaison Cloudflare lâche. Le carnet
  rouvre alors un tunnel et affiche la nouvelle adresse ; la page prévient et
  reprend d'elle-même dès que le serveur répond. Les vidéos produites sont
  écrites dans `/content/flambee-data`, hors du dossier cloné, et survivent
  donc à une relance de la cellule ;
- Google reprend la machine après quelques heures : **télécharger la vidéo
  avant la fin de la session** ;
- depuis une IP Google, les plateformes bloquent presque toujours yt-dlp :
  passer par l'import de fichiers (voir ci-dessous) ;
- l'encodage est plus lent que sur une machine dédiée : utiliser l'aperçu 540p
  pour itérer.

## Sans ordinateur : héberger Flambée soi-même

Flambée a besoin de Python, ffmpeg et yt-dlp : elle ne tourne pas sur iOS. Si tu
n'as pas de machine, elle peut vivre sur un petit serveur que tu pilotes depuis
Safari. Deux choses sont alors indispensables — et fournies :

- **un mot de passe** : sans lui, n'importe qui pourrait lancer des rendus.
  ```bash
  FLAMBEE_PASSWORD="ton-mot-de-passe" FLAMBEE_HOST=0.0.0.0 ./run.sh
  ```
  Le navigateur demande l'identifiant (`flambee` par défaut, modifiable avec
  `FLAMBEE_USERNAME`) et le retient ensuite.

- **l'import de fichiers** : depuis une adresse IP de datacenter, YouTube et
  TikTok bloquent très souvent yt-dlp (« Sign in to confirm you're not a bot »),
  y compris avec des cookies. À l'étape 1, *Choisir des vidéos* envoie donc des
  vidéos déjà présentes sur le téléphone (pellicule ou Fichiers) : le reste du
  pipeline est identique. Plafond réglable avec `FLAMBEE_MAX_UPLOAD_MB`
  (600 Mo par défaut).

Un serveur derrière une adresse publique doit aussi être servi en HTTPS (un
reverse proxy type Caddy suffit) : sinon le mot de passe circule en clair.

## Piloter Flambée depuis un iPhone

L'outil a besoin de Python, ffmpeg et yt-dlp : il tourne **sur le Mac**, pas sur
le téléphone. En revanche l'interface est utilisable depuis un iPhone sur le
même Wi-Fi.

```bash
FLAMBEE_HOST=0.0.0.0 ./run.sh
```

Le script affiche l'adresse à taper dans Safari, du type `http://192.168.1.20:8000`.
Sur l'iPhone : *Partager → Sur l'écran d'accueil* pour l'ouvrir comme une app.

- Le Mac doit rester allumé et le terminal ouvert : c'est lui qui télécharge et
  encode.
- Le serveur devient accessible à **toute personne présente sur ce réseau** :
  à réserver à un Wi-Fi de confiance, et à couper (Ctrl-C) après usage.
- En Wi-Fi le presse-papier du navigateur est indisponible (connexion non
  sécurisée) : le bouton « Copier le prompt » affiche alors le texte à
  sélectionner et copier à la main.
- Pour récupérer la vidéo sur le téléphone : bouton *Télécharger le .mp4*
  (elle arrive dans Fichiers, puis *Partager → Enregistrer dans Photos*).
  Depuis le Mac, AirDrop du fichier de `output/` marche tout aussi bien.

## Les 5 étapes

| Étape | Ce qui se passe |
|---|---|
| **1. Sources** | 2 à 5 liens collés → téléchargement `yt-dlp` dans `work/<projet>/sources`. Durée, résolution et nombre de vues sont affichés ; une vidéo de moins de 45 s déclenche un avertissement. **Ou** import direct de vidéos depuis l'appareil, quand la plateforme refuse le téléchargement. |
| **2. Accroche** | Les 3 premières secondes de chaque source sont extraites (ffmpeg) et jouées côte à côte. Un clic choisit celle qui ouvrira le montage. |
| **3. Style** | Voix `edge-tts` (+ débit), **six styles de sous-titres**, musique de fond et son volume, travelling, coupes calées sur les plans, masquage (flou ou bandeau noir) des sous-titres incrustés dans les sources, fond d'ambiance des vidéos d'origine. |
| **4. Script** | Sujet + consignes → appel à l'API Claude, **ou** bouton « Copier le prompt » pour le coller dans une conversation Claude et rapporter le texte. Le script reste éditable, avec un compteur de mots et la durée estimée. |
| **5. Rendu** | Voix off → sous-titres `.ass` calés au mot → découpe, recadrage 9:16, montage, mixage et encodage **en une seule passe ffmpeg** → `.mp4` dans `output/`. Aperçu 540p en quelques secondes, barre de progression réelle, bouton d'annulation. |

## Comment c'est monté

- La **durée de la vidéo suit la voix off** : le script est synthétisé d'abord, puis
  `trimmer.plan_segments()` répartit les extraits (2,5 à 6 s chacun) pour couvrir
  exactement cette durée, en alternant les sources et en évitant de repasser deux
  fois au même endroit.
- Les **coupes tombent sur les changements de plan** : `analyzer` détecte les
  scènes de chaque source (décodage en 192 px de large, donc très rapide) et le
  planificateur y recale les entrées/sorties d'extrait.
- Les **sous-titres** utilisent les évènements `WordBoundary` d'edge-tts : pas de
  transcription, un calage au mot exact, rendu façon TikTok (ligne complète, mot
  actif surligné, léger « pop » en début de ligne). Trois styles au choix.
  edge-tts place son premier mot à 0,000 s alors que l'audio commence par un
  court silence : celui-ci est mesuré (`voice.measure_lead_in`) et les
  sous-titres sont décalés d'autant, sans quoi tout le texte passe en avance
  d'environ deux dixièmes de seconde.
- L'**accroche recommandée** est celle qui combine le plus de mouvement,
  d'énergie sonore et de vues sur ses 3 premières secondes.
- Le **son** est traité comme sur une vraie vidéo virale : voix normalisée en
  EBU R128 (−14 LUFS), musique qui s'efface automatiquement sous la voix
  (`sidechaincompress`), limiteur en sortie.

## Les six styles de sous-titres

| Style | Police | Caractère |
|---|---|---|
| **Punch** | Archivo Black | Le classique viral : gros, blanc, mot actif ambre. |
| **Impact** | Anton | Capitales condensées, ton affirmé. |
| **Néon** | Archivo Black | Halo lumineux sur le mot prononcé. |
| **Studio** | Archivo Black | Bandeau sombre : lisible sur n'importe quelle image. |
| **Signature** | Playfair Display | Serif haut de gamme, or discret. |
| **Minimal** | Bebas Neue | Capitales espacées, sans animation. |

Les quatre polices (SIL OFL) sont **embarquées dans `assets/fonts/`** et passées
à ffmpeg via `fontsdir`. Sans elles, ffmpeg retombe sur une police système et
les sous-titres perdent tout leur caractère — c'est ce qui se produit sur une
machine neuve ou sur Colab.

## L'interface

Sombre, chaude, sans dépendance externe : les polices de l'interface (Inter,
Instrument Serif) sont servies depuis `flambee/static/fonts/` en woff2, donc
aucun appel réseau et aucun clignotement au chargement. Le tout tient dans un
seul fichier CSS piloté par variables — un thème se change en éditant les
tokens en haut de `style.css`.

## Optimisations

Le rendu est la seule opération vraiment coûteuse : tout est organisé pour la
réduire.

| Levier | Effet |
|---|---|
| **Rendu en une passe** | Découpe, recadrage, travelling, masque, concaténation, sous-titres et mixage tiennent dans un seul `-filter_complex`. La vidéo n'est encodée **qu'une fois** au lieu de N+1 : plus rapide, et sans perte de génération. |
| **Encodeur matériel** | Détecté au premier lancement en testant réellement chaque codec (VideoToolbox sur Mac, NVENC, QuickSync), avec repli `libx264`. Le résultat est mis en cache. |
| **Aperçu 540p** | Même montage, quatre fois moins de pixels : ~3× plus rapide pour valider un montage avant le rendu définitif. |
| **Parallélisme** | Téléchargements simultanés, accroches et analyse des plans extraites en parallèle sur tous les cœurs. |
| **Voix mise en cache** | Tant que le script et la voix ne changent pas, la piste et son minutage sont réutilisés — un rendu final qui suit un aperçu ne rappelle pas edge-tts. |
| **Flou économique** | Le masque des sous-titres sources passe par une réduction/agrandissement plutôt que `boxblur` : rendu équivalent, ~20 % de temps de rendu en moins. |
| **Analyse à basse résolution** | Détection de plans et mesure d'énergie sur un flux de 192 px de large. |

Ordres de grandeur mesurés sur 4 cœurs sans GPU (30 s de vidéo, 6 plans, tout
activé) : **19 s** en 1080×1920, **9 s** pour l'aperçu 540p. Sur un Mac récent,
VideoToolbox réduit encore nettement la partie encodage.

## Arborescence

```
flambee/
  config.py       réglages, formats, styles de sous-titres, prompt système
  media.py        helpers ffmpeg (encodeur, progression, filtres 9:16, masque)
  analyzer.py     détection des plans + score d'accroche
  downloader.py   étape 1 — wrapper yt-dlp
  auth.py         mot de passe optionnel (HTTP Basic)
colab/            carnet Colab + lanceur (serveur derrière un tunnel HTTPS)
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
| `FLAMBEE_HOST` | `127.0.0.1` | `0.0.0.0` pour ouvrir l'accès au réseau local (téléphone) |
| `FLAMBEE_PASSWORD` | — | protège l'accès (obligatoire dès que l'app sort de la machine) |
| `FLAMBEE_USERNAME` | `flambee` | identifiant associé au mot de passe |
| `FLAMBEE_MAX_UPLOAD_MB` | `600` | taille maximale d'une vidéo importée |
| `FLAMBEE_NICE` | `0` | priorité de l'encodage (`10` sur une machine distante, pour ne pas étrangler le réseau) |
| `FLAMBEE_OUTPUT_DIR` | `./output` | dossier des rendus |
| `FLAMBEE_WORK_DIR` | `./work` | fichiers de travail |
| `FLAMBEE_MIN_DURATION` | `45` | seuil d'avertissement sur les sources |
| `FLAMBEE_HOOK_DURATION` | `3` | durée de l'accroche |
| `FLAMBEE_ANTHROPIC_MODEL` | `claude-sonnet-5` | modèle utilisé pour le script |
| `FLAMBEE_ENCODER` | auto | force un encodeur (`libx264`, `h264_videotoolbox`, `h264_nvenc`…) |
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
- **Rendu long** → regarde l'encodeur annoncé dans le bandeau : s'il affiche
  `libx264` sur un Mac récent, VideoToolbox n'a pas été détecté (ffmpeg compilé
  sans). Utilise l'aperçu 540p pour itérer, et le rendu complet une fois le
  montage validé.

## Usage

Outil personnel. Télécharger des vidéos TikTok/YouTube va à l'encontre des CGU de
ces plateformes : garde les sources en local, ne republie pas le contenu d'autrui
tel quel, ne redistribue pas l'outil. Vérifie aussi la licence des musiques que tu
ajoutes dans `assets/music/`.
