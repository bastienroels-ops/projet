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
from pathlib import Path

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
        "FLAMBEE_PASSWORD": password,
        "FLAMBEE_USERNAME": username,
        "FLAMBEE_HOST": "127.0.0.1",
        "FLAMBEE_PORT": str(port),
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
    return process, url


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


def banner(url: str, username: str, password: str) -> str:
    line = "═" * 54
    return "\n".join([
        "", line,
        "  🔥  FLAMBÉE EST EN LIGNE",
        line,
        f"  {'Adresse':<15}{url}",
        f"  {'Identifiant':<15}{username}",
        f"  {'Mot de passe':<15}{password}",
        f"  {'Transcription':<15}{etat_transcription()}",
        line,
        "  Ouvre l'adresse dans Safari, saisis l'identifiant et le mot de",
        "  passe, puis Partager → Sur l'écran d'accueil.",
        "",
        "  ⚠️  Laisse cet onglet Colab ouvert : il fait tourner le serveur.",
        "  ⚠️  Télécharge tes vidéos avant la fin de la session Colab,",
        "      sinon elles sont perdues avec la machine.",
        line, "",
    ])


def start_all(
    port: int = 8000,
    password: str = "",
    username: str = "flambee",
    *,
    anthropic_key: str = "",
    tunnel: bool = True,
) -> tuple[subprocess.Popen, subprocess.Popen | None, str | None, str]:
    """Prépare l'environnement, démarre le serveur et (au besoin) le tunnel.

    Retourne (serveur, tunnel, adresse publique, mot de passe). L'adresse vaut
    None si le tunnel n'a pas pu s'ouvrir : le serveur tourne quand même, et
    reste joignable par le lien de secours de Colab.
    """
    ensure_ffmpeg()
    ensure_dependencies()
    password = password or generate_password()

    server = start_server(port, password, username, anthropic_key)
    if not wait_for_server(port):
        output = (server.stdout.read() if server.stdout else "")[-2000:]
        server.terminate()
        raise RuntimeError(f"Le serveur n'a pas démarré :\n{output}")

    if not tunnel:
        return server, None, None, password

    binary = ensure_cloudflared(ROOT / "colab" / "cloudflared")
    log("→ Ouverture du tunnel HTTPS…")
    try:
        tunnel_process, url = start_tunnel(binary, port)
        return server, tunnel_process, url, password
    except RuntimeError as exc:
        log(f"⚠️  {exc}")
        return server, None, None, password


def keep_alive(
    server: subprocess.Popen,
    tunnel: subprocess.Popen | None,
    *,
    port: int = 8000,
    username: str = "flambee",
    password: str = "",
) -> None:
    """Maintient la cellule active et remet le tunnel debout s'il tombe.

    Un encodage long sature la machine : le tunnel peut perdre sa liaison avec
    Cloudflare (erreurs 530/1033 dans le navigateur). Le rendu, lui, continue
    côté serveur — il suffit de rouvrir un tunnel et de reprendre.
    """
    binary = ROOT / "colab" / "cloudflared"
    try:
        while True:
            time.sleep(20)
            if server.poll() is not None:
                log("❌ Le serveur s'est arrêté. Relance la cellule.")
                return
            if tunnel is not None and tunnel.poll() is not None:
                log("⚠️  Tunnel interrompu (l'encodage a saturé la machine). "
                    "Réouverture…")
                try:
                    tunnel, url = start_tunnel(binary, port)
                    log(banner(url, username, password))
                    log("  ⚠️  L'adresse a changé : utilise la nouvelle "
                        "ci-dessus. Ton travail en cours est intact.\n")
                except RuntimeError as exc:
                    log(f"⚠️  Réouverture impossible ({exc}). "
                        "Utilise le lien de secours Colab.")
                    tunnel = None
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

    secours = colab_fallback_url(args.port)

    if url:
        log(banner(url, args.username, password))
    elif secours:
        log("\n⚠️  Le tunnel ne s'est pas ouvert — utilise le lien de secours.")
        log(f"   Identifiant : {args.username} — Mot de passe : {password}\n")
    else:
        log(f"\nServeur démarré sur http://127.0.0.1:{args.port} "
            f"(identifiant {args.username}, mot de passe {password}).")

    if secours:
        log(f"  Lien de secours Colab : {secours}\n")

    def stop(*_args) -> None:
        for process in (tunnel, server):
            if process is not None and process.poll() is None:
                process.terminate()
        log("\nFlambée arrêtée.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    keep_alive(server, tunnel, port=args.port, username=args.username,
               password=password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
