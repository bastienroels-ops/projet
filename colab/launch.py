"""Démarrage de Flambée sur Google Colab (ou toute machine sans écran).

Lance le serveur puis ouvre un tunnel HTTPS Cloudflare, et affiche l'adresse à
ouvrir dans Safari. Le tunnel est gratuit et ne demande aucun compte ; l'adresse
change à chaque démarrage.

    python colab/launch.py                  # mot de passe engendré et affiché
    python colab/launch.py --password ...   # mot de passe imposé
    python colab/launch.py --check          # vérifications seules, sans serveur
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path


def _heure_locale(fuseau: str = "Europe/Paris") -> None:
    """Met la machine à l'heure de l'utilisateur, avant tout le reste.

    Colab tourne en UTC. Une machine de Londres qui affiche ses journaux à
    10:30 pendant qu'il est midi à Paris n'a l'air ni d'un décalage horaire ni
    d'un réglage : elle a l'air d'une session morte depuis deux heures. C'est
    exactement la conclusion qu'on en tire, et on va chercher une panne qui
    n'existe pas.

    Posé ici, le fuseau vaut pour tout : l'horodatage des journaux, la date
    d'inscription d'un compte, le mois que regarde le décompte de crédits.
    """
    os.environ.setdefault("TZ", fuseau)
    try:
        time.tzset()
    except AttributeError:
        pass          # Windows ne connaît pas tzset ; Colab est sous Linux.


_heure_locale()

ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64"
)
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Mots simples : un mot de passe qui se retape sans erreur sur un clavier tactile.
_WORDS = (
    "ananas banane cerise datte figue goyave kiwi litchi mangue nectarine "
    "orange papaye pastèque poire pomme prune raisin tomate abricot melon"
).split()


def log(message: str = "") -> None:
    print(message, flush=True)


def generate_password(words: int = 3) -> str:
    """Mot de passe lisible, du type « kiwi-mangue-poire »."""
    return "-".join(secrets.SystemRandom().sample(_WORDS, words))


def extract_tunnel_url(text: str) -> str | None:
    """Récupère l'adresse publique dans la sortie de cloudflared."""
    match = TUNNEL_RE.search(text)
    return match.group(0) if match else None


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    log("→ Installation de ffmpeg…")
    subprocess.run(["apt-get", "-qq", "update"], check=False)
    subprocess.run(["apt-get", "-qq", "install", "-y", "ffmpeg"], check=True)


def ensure_dependencies() -> None:
    try:
        import fastapi  # noqa: F401
        import yt_dlp  # noqa: F401
        import edge_tts  # noqa: F401
    except ImportError:
        log("→ Installation des dépendances Python…")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r",
             str(ROOT / "requirements.txt")],
            check=True,
        )


def ensure_transcription() -> bool:
    """Installe puis vérifie le moteur de transcription. Retourne sa disponibilité.

    L'appel à pip était auparavant lancé avec `check=False` : un échec passait
    inaperçu, et la panne n'apparaissait que plus tard dans l'application, sous
    la forme d'un « moteur absent » sans explication. On installe donc ici en
    vérifiant le résultat par un import réel, et l'on dit ce qui a manqué.
    """
    ensure_dependencies()
    preparer_environnement()          # avant le premier import de `flambee`
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from flambee import transcribe

    if transcribe.available():
        log(f"→ Transcription : {transcribe.moteur_actif()} déjà en place.")
        return True

    log("→ Installation du moteur de transcription (une à trois minutes)…")
    ok, journal = transcribe.installer()
    if ok:
        log(f"→ Transcription : {transcribe.moteur_actif()} prêt.")
        return True

    log("")
    log("⚠️  Le moteur de transcription n'a pas pu être installé.")
    log("    Script Viral et Voice Studio resteront verrouillés ; le reste")
    log("    de Flambée fonctionne normalement.")
    log("    Tu peux réessayer depuis l'application : la page de ces rubriques")
    log("    propose un bouton « Installer le moteur ».")
    log("    Détail des tentatives :")
    for ligne in journal.splitlines():
        log(f"      {ligne}")
    log("")
    return False


def ensure_cloudflared(destination: Path) -> Path:
    """Télécharge le binaire du tunnel s'il n'est pas déjà là."""
    existing = shutil.which("cloudflared")
    if existing:
        return Path(existing)
    if destination.exists() and destination.stat().st_size > 1_000_000:
        return destination

    log("→ Téléchargement de cloudflared…")
    destination.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(CLOUDFLARED_URL, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IEXEC)
    return destination


def wait_for_server(port: int, timeout: float = 60.0) -> bool:
    """Attend que le serveur réponde avant d'ouvrir le tunnel."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def data_dir() -> Path:
    """Dossier des rendus et fichiers de travail, hors du dossier cloné.

    Relancer la cellule efface le clone : y laisser les vidéos produites
    reviendrait à les perdre au premier redémarrage.
    """
    base = Path("/content") if Path("/content").is_dir() else ROOT
    folder = base / "flambee-data"
    (folder / "output").mkdir(parents=True, exist_ok=True)
    (folder / "work").mkdir(parents=True, exist_ok=True)
    (folder / "music").mkdir(parents=True, exist_ok=True)
    return folder


def mot_de_passe_persistant(fourni: str = "") -> str:
    """Le même mot de passe d'un lancement à l'autre, tant que la machine vit.

    Il était engendré à neuf à chaque exécution de la cellule : Safari ne
    pouvait donc jamais le retenir, et il fallait le recopier à la main après
    chaque relance — y compris celles dues à une chute de tunnel. Il est
    désormais conservé à côté des vidéos.

    Une nouvelle machine Colab repart de zéro : son disque est effacé avec
    elle. Pour un mot de passe vraiment stable, il faut le saisir dans la
    cellule, où il a priorité sur celui-ci.
    """
    fichier = data_dir() / ".motdepasse"
    if fourni:
        fichier.write_text(fourni, encoding="utf-8")
    elif fichier.exists():
        return fichier.read_text(encoding="utf-8").strip() or generate_password()
    else:
        fourni = generate_password()
        fichier.write_text(fourni, encoding="utf-8")
    try:
        fichier.chmod(0o600)
    except OSError:                    # systèmes sans permissions POSIX
        pass
    return fourni


def preparer_environnement() -> Path:
    """Fixe les dossiers de travail avant que `flambee.config` soit importé.

    `config` lit ces variables une seule fois, à l'import. Or la vérification
    du moteur de transcription importe `flambee.transcribe` — donc `config` —
    bien avant qu'on ait besoin de la base des comptes. Quand le réglage
    arrivait après, il ne servait plus à rien : le dossier restait celui du
    dépôt cloné, et le compte créé automatiquement atterrissait dans une base
    que le serveur n'ouvrirait jamais. Ce compte n'a donc jamais existé sur
    Colab, sans le moindre message.

    Appelé au tout début, et avant chaque import de `flambee` par précaution.
    """
    data = data_dir()
    os.environ.setdefault("FLAMBEE_WORK_DIR", str(data / "work"))
    os.environ.setdefault("FLAMBEE_OUTPUT_DIR", str(data / "output"))
    return data


def start_server(
    port: int,
    password: str,
    username: str,
    anthropic_key: str = "",
) -> subprocess.Popen:
    data = data_dir()
    environment = {
        **os.environ,
        "FLAMBEE_OUTPUT_DIR": str(data / "output"),
        "FLAMBEE_WORK_DIR": str(data / "work"),
        # Encodage en priorité basse : sinon ffmpeg monopolise les deux cœurs
        # de la machine et le tunnel finit par tomber en plein rendu.
        "FLAMBEE_NICE": os.environ.get("FLAMBEE_NICE", "10"),
        # Colab annonce parfois plus de cœurs qu'il n'en donne réellement.
        # On impose donc la limite plutôt que de la laisser deviner : un
        # encodage qui prend tout fait tomber le tunnel en plein rendu.
        "FLAMBEE_FFMPEG_THREADS": os.environ.get("FLAMBEE_FFMPEG_THREADS", "1"),
        "FLAMBEE_PASSWORD": password,
        "FLAMBEE_USERNAME": username,
        "FLAMBEE_AUTO_SESSION": os.environ.get("FLAMBEE_AUTO_SESSION", ""),
        "FLAMBEE_HOST": "127.0.0.1",
        "FLAMBEE_PORT": str(port),
        # Le lien direct de Colab, transmis à l'application : quand le tunnel
        # tombe, la page affiche ce chemin-là plutôt qu'un mur.
        "FLAMBEE_PORTE_DIRECTE": os.environ.get("FLAMBEE_PORTE_DIRECTE", ""),
        "PYTHONUNBUFFERED": "1",
    }
    if anthropic_key.strip():
        # Active la génération de script en un clic ; sans elle, l'app bascule
        # sur le mode manuel (prompt à copier dans une conversation Claude).
        environment["ANTHROPIC_API_KEY"] = anthropic_key.strip()
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "flambee.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )


def start_tunnel(binary: Path, port: int, timeout: float = 90.0):
    """Ouvre le tunnel et retourne (processus, adresse publique)."""
    # --protocol http2 : le transport par défaut (QUIC, en UDP) est filtré sur
    # beaucoup de réseaux ; le tunnel s'enregistre alors sans jamais devenir
    # joignable (erreur Cloudflare 1033).
    process = subprocess.Popen(
        [str(binary), "tunnel", "--no-autoupdate", "--protocol", "http2",
         "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    url: str | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and url is None:
        line = process.stdout.readline() if process.stdout else ""
        if not line:
            if process.poll() is not None:
                break
            continue
        url = extract_tunnel_url(line)

    if url is None:
        process.terminate()
        raise RuntimeError(
            "Le tunnel Cloudflare n'a pas démarré. Relance la cellule ; si le "
            "problème persiste, utilise le lien de secours affiché plus haut."
        )

    # On continue à vider la sortie du tunnel, sinon le tuyau finit par bloquer.
    threading.Thread(target=_drain, args=(process,), daemon=True).start()
    _prioriser(process)
    return process, url


def _prioriser(process: subprocess.Popen) -> None:
    """Donne au tunnel la priorité sur l'encodage.

    Le tunnel n'a presque rien à faire, mais il doit le faire à l'heure :
    quelques battements de cœur manqués et Cloudflare coupe la liaison
    (erreur 1033). L'encodage, lui, peut attendre quelques millisecondes sans
    que personne ne s'en aperçoive. Sur Colab on est root, donc la priorité
    négative passe ; ailleurs elle est refusée, et ce n'est pas grave — la
    limite de cœurs suffit déjà.
    """
    try:
        os.setpriority(os.PRIO_PROCESS, process.pid, -5)
    except (OSError, AttributeError, PermissionError):
        pass


# Les dernières lignes du serveur, gardées pour les afficher en cas d'échec.
_JOURNAL: deque[str] = deque(maxlen=300)


def relayer(process: subprocess.Popen, etiquette: str = "serveur") -> None:
    """Recopie la sortie du serveur dans la cellule, ligne par ligne.

    Deux raisons, et la seconde est la plus grave. D'abord, sans cela une
    erreur 500 s'affiche dans le navigateur sans que personne ne puisse en
    connaître la cause : la trace part dans un tuyau que rien ne lit. Ensuite,
    ce tuyau a un fond — environ 64 ko — et quand il est plein le serveur se
    bloque en essayant d'y écrire. L'application cesse alors de répondre, sans
    s'être arrêtée, et tout échoue en même temps : la page comme ses styles.
    """
    def boucle() -> None:
        for ligne in iter(process.stdout.readline, ""):   # type: ignore[union-attr]
            ligne = ligne.rstrip()
            if not ligne:
                continue
            _JOURNAL.append(ligne)
            log(f"  [{etiquette}] {ligne}")
    threading.Thread(target=boucle, daemon=True).start()


def journal_recent(lignes: int = 40) -> str:
    """Les dernières lignes vues, pour un message d'échec qui dit quelque chose."""
    return "\n".join(list(_JOURNAL)[-lignes:])


def _drain(process: subprocess.Popen) -> None:
    for _ in iter(process.stdout.readline, ""):  # type: ignore[union-attr]
        if process.poll() is not None:
            return


def colab_fallback_url(port: int) -> str | None:
    """Lien de secours propre à Colab, qui ne passe par aucun tunnel.

    Il ne fonctionne que dans le navigateur connecté au compte Google, ce qui
    en fait aussi le plus sûr des deux.
    """
    try:
        from google.colab.output import eval_js  # type: ignore

        return eval_js(f"google.colab.kernel.proxyPort({port})")
    except Exception:
        return None


def etat_transcription() -> str:
    """Une ligne pour le bandeau : le moteur est-il prêt, et lequel.

    Sans cela, l'absence de moteur ne se découvre qu'en ouvrant Script Viral,
    longtemps après le démarrage.
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from flambee import transcribe
    except Exception:
        return "état inconnu"

    if transcribe.available():
        return f"{transcribe.moteur_actif()} ✓"
    return "absente — bouton « Installer le moteur » dans l'application"


# Le lien direct de Colab et le compte créés au démarrage, lus par `banner()`.
_SECOURS: str | None = None

# Passer par une variable plutôt que par la valeur de retour de `start_all()`
# est délibéré : Colab garde en mémoire la cellule affichée dans le navigateur,
# qui peut dater d'une version antérieure. Changer la signature casserait ces
# carnets-là, alors que ce fichier, lui, vient d'être récupéré.
_COMPTE: str | None = None

# L'adresse publique du tunnel, tenue à jour à chaque réouverture. Même
# raison que ci-dessus, et une conséquence concrète : la cellule que Colab
# garde en mémoire appelle `keep_alive(serveur, tunnel, port=…, password=…)`
# sans lui passer `url`. Sans ce global, la surveillance du tunnel ne
# s'exécutait jamais chez qui a déjà lancé le carnet une fois.
_ADRESSE: str = ""

COMPTE_COLAB = "moi@flambee.local"


def ouvrir_un_compte(password: str) -> str | None:
    """Crée le compte de la session et retourne son adresse.

    Sur une machine Colab, tout est effacé à la fin : remplir un formulaire
    d'inscription à chaque démarrage n'ajoute aucune sécurité — l'accès est
    déjà fermé par le mot de passe du tunnel — et fait une corvée de plus
    avant de pouvoir travailler. Le compte reçoit le même mot de passe que le
    tunnel : il n'y a ainsi qu'un secret à retenir, et il tient déjà dans
    l'encadré.

    Étant le premier compte, il est celui de l'administrateur : crédits
    illimités et toutes les rubriques ouvertes.
    """
    dossier = Path(preparer_environnement() / "work")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from flambee import config, users
    except Exception as exc:                       # jamais bloquant
        log(f"⚠️  Compte automatique impossible : {exc}")
        return None

    if config.WORK_DIR != dossier:
        # Filet de sécurité : `preparer_environnement()` doit avoir été appelé
        # avant le premier import de `flambee`. Si ce n'est pas le cas, le
        # compte irait dans une base que le serveur n'ouvrira pas. Mieux vaut
        # ne rien faire que d'afficher des identifiants qui ne mènent nulle
        # part — mais c'est un défaut de code, pas une situation normale.
        log(f"⚠️  Dossier de travail figé sur {config.WORK_DIR} au lieu de "
            f"{dossier} : inscription manuelle.")
        return None

    try:
        if users.par_email(COMPTE_COLAB) is None:
            users.creer(COMPTE_COLAB, password, nom="Toi")
        return COMPTE_COLAB
    except Exception as exc:
        log(f"⚠️  Compte automatique impossible : {exc}")
        return None


# Largeur du cadre. Mesurée sur l'écran d'un iPhone, dans la sortie d'une
# cellule Colab : au-delà, les lignes sortent du cadre et le bloc se met à
# défiler horizontalement. On perd alors le début de chaque ligne, ce qui rend
# la sortie illisible sans qu'on comprenne pourquoi.
LARGEUR = 34


def banner(url: str, username: str, password: str) -> str:
    """Le cadre affiché quand tout est prêt.

    Écrit pour un téléphone tenu à la main, pas pour un terminal large.

    Les adresses sont seules sur leur ligne et commencent à la première
    colonne. Elles sont plus longues que l'écran quoi qu'on fasse — une
    adresse `trycloudflare` fait une cinquantaine de caractères — mais
    commencer à gauche garantit qu'on en voit le début sans rien faire
    défiler, et qu'on peut les toucher. Collées après une étiquette et quinze
    espaces, comme avant, elles commençaient hors de l'écran : on ne pouvait
    ni les lire ni les toucher, seulement les recopier de travers.

    Deux adresses, et la directe en premier. Elles mènent au même serveur par
    deux chemins qui n'ont rien en commun : celle de Colab ne traverse pas
    Cloudflare, donc ni 530 ni 1033, jamais — tandis que le tunnel, lui,
    tombe. Présenter la fragile en tête, comme on le faisait, revenait à
    envoyer l'utilisateur droit sur la seule des deux qui puisse afficher un
    mur en anglais.
    """
    trait = "═" * LARGEUR
    heure = time.strftime("%H:%M")

    lignes = [
        "", trait,
        "  🔥  FLAMBÉE EST EN LIGNE",
        f"      Lancé à {heure}",
        trait,
        "",
    ]

    if _SECOURS:
        lignes += [
            "  👉 DEPUIS CE TÉLÉPHONE,",
            "     TOUCHE CE LIEN :",
            "",
            _SECOURS,
            "",
            "  Il ne passe par aucun tunnel :",
            "  ni erreur 530, ni erreur 1033.",
            "",
            "  ─────────────────────────",
            "",
            "  Depuis un autre appareil :",
            "",
            url,
            "",
        ]
    else:
        lignes += [
            "  👉 TOUCHE LE LIEN BLEU :",
            "",
            url,
            "",
        ]

    lignes += [
        f"  Identifiant     {username}",
        f"  Mot de passe    {password}",
        "",
    ]

    if _COMPTE:
        lignes += [
            "  Ton compte est déjà créé.",
            "  Touche « Connexion » et saisis :",
            "",
            f"  {_COMPTE}",
            f"  {password}   (le même)",
            "",
        ]

    lignes += [
        f"  Transcription   {etat_transcription()}",
        "",
    ]

    if _SECOURS:
        lignes += [
            "  ⚠️  « Error 530 » ou « 1033 » sur",
            "      une adresse ? Prends l'autre :",
            "      c'est le même atelier, et ton",
            "      travail est intact.",
            "",
        ]

    lignes += [
        "  ⚠️  Garde cet onglet ouvert :",
        "      il fait tourner le serveur.",
        "  ⚠️  Télécharge tes vidéos avant",
        "      la fin de la session.",
        trait, "",
    ]
    return "\n".join(lignes)


def porte_directe() -> str:
    """L'adresse qui ne traverse aucun tunnel, ou une chaîne vide.

    Une fonction plutôt qu'un accès direct à `_SECOURS` : le carnet Colab est
    la seule chose que je ne puisse pas mettre à jour à distance — sa cellule
    est gardée en mémoire par le navigateur. Une fonction, elle, garde son
    nom quoi qu'il arrive derrière.
    """
    return _SECOURS or ""


def _bouton(url: str, titre: str, sous_titre: str, principal: bool) -> str:
    """Le HTML d'un bouton-lien, dans le carnet."""
    fond = ("linear-gradient(168deg,#6c5ce7,#3b2b8f)" if principal
            else "rgba(108,92,231,.14)")
    bordure = "none" if principal else "1px solid rgba(167,139,250,.45)"
    couleur = "#f6f4ff" if principal else "#c9c2f0"
    ombre = ("box-shadow:0 8px 20px -10px rgba(108,92,231,.9);"
             if principal else "")
    return (
        f'<a href="{url}" target="_blank" rel="noopener" style="'
        'display:block;margin:14px 0;padding:18px 20px;border-radius:14px;'
        f'background:{fond};color:{couleur};border:{bordure};{ombre}'
        'font:600 17px/1.3 system-ui,-apple-system,sans-serif;'
        'text-align:center;text-decoration:none">'
        f'{titre}'
        '<div style="font:400 12px/1.5 system-ui,-apple-system,sans-serif;'
        f'opacity:.8;margin-top:4px">{sous_titre}</div>'
        '<div style="font:400 12px/1.5 ui-monospace,monospace;opacity:.6;'
        f'margin-top:6px;word-break:break-all">{url}</div></a>')


def afficher_le_lien(url: str, direct: str = "") -> None:
    """De vrais boutons, quand on est dans un carnet.

    Le cadre en texte reste la référence — il s'affiche partout. Mais dans
    Colab, une adresse imprimée est une ligne de texte de cinquante
    caractères qu'on vise au doigt ; un bouton, non. C'est le seul geste que
    l'utilisateur ait à faire, autant qu'il soit large.

    Deux boutons quand les deux chemins existent, et le direct en premier :
    c'est le seul qui ne puisse pas afficher d'erreur Cloudflare. Le second
    reste offert — il marche depuis n'importe quel appareil, pas seulement
    celui où Colab est ouvert.

    Silencieux hors carnet : le script doit tourner aussi bien depuis un
    terminal, où `IPython` n'existe pas.
    """
    try:
        from IPython.display import HTML, display
    except Exception:
        return

    direct = direct or (_SECOURS or "")
    # Deux boutons vers la même adresse — cas du tunnel qui ne s'est pas
    # ouvert, où l'on n'affiche plus que la porte directe — n'aideraient
    # personne à choisir.
    if direct and direct != url:
        display(HTML(
            _bouton(direct, "Ouvrir Flambée",
                    "depuis ce téléphone — sans tunnel", True)
            + _bouton(url, "Ouvrir depuis un autre appareil",
                      "passe par Cloudflare", False)))
    else:
        display(HTML(_bouton(url, "Ouvrir Flambée", "", True)))


def start_all(
    port: int = 8000,
    password: str = "",
    username: str = "flambee",
    *,
    anthropic_key: str = "",
    tunnel: bool = True,
    transcription: bool = True,
) -> tuple[subprocess.Popen, subprocess.Popen | None, str | None, str]:
    """Prépare l'environnement, démarre le serveur et (au besoin) le tunnel.

    Retourne (serveur, tunnel, adresse publique, mot de passe). L'adresse vaut
    None si le tunnel n'a pas pu s'ouvrir : le serveur tourne quand même, et
    reste joignable par le lien de secours de Colab.

    Le moteur de transcription est installé ici, et non dans le carnet : la
    cellule que l'on a sous les yeux dans son navigateur peut dater d'une
    version antérieure — Colab la garde en mémoire — alors que ce fichier, lui,
    vient d'être récupéré. Le faire à cet endroit garantit que Script Viral et
    Voice Studio fonctionnent même avec une vieille cellule.
    """
    preparer_environnement()          # avant tout import de `flambee`
    ensure_ffmpeg()
    ensure_dependencies()
    if transcription:
        try:
            ensure_transcription()
        except Exception as exc:        # jamais bloquant : le reste doit tourner
            log(f"⚠️  Moteur de transcription : {exc}")
    password = mot_de_passe_persistant(password.strip())

    # Le compte est créé avant le serveur : celui-ci démarre alors avec les
    # inscriptions fermées, et l'adresse publique du tunnel ne permet à
    # personne d'ouvrir un compte sur ta machine.
    global _COMPTE
    _COMPTE = ouvrir_un_compte(password)
    if _COMPTE:
        os.environ["FLAMBEE_SIGNUP"] = "ferme"
        # Le verrou du tunnel vient d'être franchi : redemander une connexion
        # par formulaire n'ajoute rien, et l'adresse changeant à chaque
        # lancement, le cookie ne survivrait pas de toute façon.
        os.environ["FLAMBEE_AUTO_SESSION"] = _COMPTE

    # Recueilli avant le serveur, et non après : c'est une variable
    # d'environnement, et `start_server` fige les siennes au démarrage. Ne
    # dépend que de Colab — la demande porte sur un numéro de port, pas sur
    # quelque chose qui écoute déjà.
    global _SECOURS
    _SECOURS = colab_fallback_url(port)
    if _SECOURS:
        os.environ["FLAMBEE_PORTE_DIRECTE"] = _SECOURS

    server = start_server(port, password, username, anthropic_key)
    # Le relais démarre avant l'attente : c'est pendant le démarrage que les
    # erreurs les plus utiles apparaissent, et il ne faut pas les manquer.
    relayer(server)
    if not wait_for_server(port):
        server.terminate()
        raise RuntimeError(f"Le serveur n'a pas démarré :\n{journal_recent()}")

    if not tunnel:
        return server, None, None, password

    binary = ensure_cloudflared(ROOT / "colab" / "cloudflared")
    log("→ Ouverture du tunnel HTTPS…")
    try:
        tunnel_process, url = start_tunnel(binary, port)
        global _ADRESSE
        _ADRESSE = url
        return server, tunnel_process, url, password
    except RuntimeError as exc:
        log(f"⚠️  {exc}")
        return server, None, None, password


# Combien de sondages ratés d'affilée avant de conclure que le tunnel est
# mort. Trois, à vingt secondes d'intervalle : une minute. En dessous on
# rouvrirait pour un hoquet, et rouvrir change l'adresse — ce qui dérange
# bien plus qu'une seconde d'interruption.
SONDAGES_AVANT_REOUVERTURE = 3
PERIODE_SONDAGE = 20.0


def tunnel_repond(url: str, timeout: float = 8.0) -> bool:
    """Le tunnel achemine-t-il encore ? Sondé de l'extérieur, par Cloudflare.

    C'est le seul moyen de distinguer les deux pannes. `cloudflared` peut
    tourner sans faute pendant que sa liaison avec l'edge est rompue : le
    navigateur voit une erreur 530, et surveiller le processus ne dit rien.

    Tout code de réponse vaut « vivant » sauf ceux que Cloudflare émet quand
    il n'atteint pas l'origine : eux seuls signent un tunnel mort.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request as Requete, urlopen

    requete = Requete(url.rstrip("/") + "/ping", method="GET")
    try:
        with urlopen(requete, timeout=timeout) as reponse:
            return reponse.status < 500
    except HTTPError as exc:
        # 401, 403, 404 : le serveur a répondu, donc le tunnel achemine.
        return exc.code not in CODES_DE_TUNNEL_MORT
    except (URLError, OSError, ValueError):
        return False


CODES_DE_TUNNEL_MORT = frozenset(
    {502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530})


def keep_alive(
    server: subprocess.Popen,
    tunnel: subprocess.Popen | None,
    *,
    port: int = 8000,
    username: str = "flambee",
    password: str = "",
    url: str = "",
) -> None:
    """Maintient la cellule active et remet le tunnel debout s'il tombe.

    Un encodage long sature la machine : le tunnel peut perdre sa liaison avec
    Cloudflare (erreurs 530/1033 dans le navigateur). Le rendu, lui, continue
    côté serveur — il suffit de rouvrir un tunnel et de reprendre.

    Deux pannes distinctes, et la seconde était invisible : le processus qui
    s'arrête, et le processus qui tourne pendant que sa liaison est rompue.
    Seule la première était surveillée. On sonde donc l'adresse publique.
    """
    binary = ROOT / "colab" / "cloudflared"
    # Une cellule Colab d'une version antérieure n'a pas d'argument `url` à
    # passer : sans ce repli, elle surveillerait un tunnel dont elle ignore
    # l'adresse, c'est-à-dire pas du tout.
    url = url or _ADRESSE
    muets = 0
    try:
        while True:
            time.sleep(PERIODE_SONDAGE)
            if server.poll() is not None:
                log("❌ Le serveur s'est arrêté. Relance la cellule.")
                return
            if tunnel is None:
                continue

            mort = tunnel.poll() is not None
            if not mort and url:
                if tunnel_repond(url):
                    if muets:
                        log("✅ Le tunnel a repris tout seul — l'adresse n'a "
                            "pas changé.")
                    muets = 0
                    continue
                muets += 1
                log(f"⚠️  Le tunnel ne répond plus ({muets}/"
                    f"{SONDAGES_AVANT_REOUVERTURE}). Le rendu, lui, continue.")
                mort = muets >= SONDAGES_AVANT_REOUVERTURE
            if not mort:
                continue

            log("⚠️  Tunnel interrompu. Réouverture…")
            if tunnel.poll() is None:
                # Il tourne encore mais n'achemine plus : le laisser vivant
                # laisserait deux tunnels concurrents sur le même port.
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
            try:
                tunnel, url = start_tunnel(binary, port)
                globals()["_ADRESSE"] = url
                muets = 0
                log(banner(url, username, password))
                afficher_le_lien(url)
                if _SECOURS:
                    log("  ⚠️  L'adresse en trycloudflare a changé. Celle du "
                        "haut, elle, n'a pas bougé — c'est tout l'intérêt "
                        "d'en avoir deux. Ton travail est intact.\n")
                else:
                    log("  ⚠️  L'adresse a changé : utilise la nouvelle "
                        "ci-dessus. Ton travail en cours est intact.\n")
            except RuntimeError as exc:
                log(f"⚠️  Réouverture impossible ({exc}). "
                    "Utilise le lien de secours Colab.")
                tunnel, url = None, ""
    except KeyboardInterrupt:
        for process in (tunnel, server):
            if process is not None and process.poll() is None:
                process.terminate()
        log("\nFlambée arrêtée.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Lance Flambée derrière un tunnel HTTPS.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--password", default=os.environ.get("FLAMBEE_PASSWORD", ""))
    parser.add_argument("--username", default=os.environ.get("FLAMBEE_USERNAME", "flambee"))
    parser.add_argument("--anthropic-key", default="",
                        help="clé API Anthropic (sinon : mode manuel)")
    parser.add_argument("--no-tunnel", action="store_true",
                        help="démarre le serveur seul, sans tunnel")
    parser.add_argument("--check", action="store_true",
                        help="vérifie l'environnement sans rien démarrer")
    args = parser.parse_args()

    if args.check:
        ensure_ffmpeg()
        ensure_dependencies()
        binary = ensure_cloudflared(ROOT / "colab" / "cloudflared")
        log(f"✅ ffmpeg : {shutil.which('ffmpeg')}")
        log(f"✅ cloudflared : {binary}")
        log("✅ dépendances Python présentes")
        return 0

    try:
        server, tunnel, url, password = start_all(
            args.port, args.password, args.username,
            anthropic_key=args.anthropic_key, tunnel=not args.no_tunnel,
        )
    except RuntimeError as exc:
        log(f"❌ {exc}")
        return 1

    # `start_all` l'a déjà recueilli, et l'a passé au serveur : le redemander
    # ici ouvrirait la porte à deux valeurs divergentes.
    secours = _SECOURS

    if url:
        log(banner(url, args.username, password))
        afficher_le_lien(url)
    elif secours:
        log("\n⚠️  Le tunnel ne s'est pas ouvert, mais le lien direct de "
            "Colab, lui, fonctionne :")
        log(f"\n{secours}\n")
        log(f"   Identifiant : {args.username} — Mot de passe : {password}\n")
        afficher_le_lien(secours)
    else:
        log(f"\nServeur démarré sur http://127.0.0.1:{args.port} "
            f"(identifiant {args.username}, mot de passe {password}).")

    def stop(*_args) -> None:
        for process in (tunnel, server):
            if process is not None and process.poll() is None:
                process.terminate()
        log("\nFlambée arrêtée.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    keep_alive(server, tunnel, port=args.port, username=args.username,
               password=password, url=url or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
