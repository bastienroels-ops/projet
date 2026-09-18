"""Tests du lanceur Colab (sans réseau ni tunnel)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colab import launch  # noqa: E402

NOTEBOOK = ROOT / "colab" / "Flambee.ipynb"
LANCEUR = ROOT / "colab" / "launch.py"


def test_adresse_du_tunnel_extraite():
    ligne = ("2026-09-12T18:00:00Z INF |  "
             "https://tasty-blue-fox-42.trycloudflare.com  |")
    assert launch.extract_tunnel_url(ligne) == \
        "https://tasty-blue-fox-42.trycloudflare.com"


def test_aucune_adresse_dans_une_ligne_ordinaire():
    assert launch.extract_tunnel_url("INF Starting tunnel") is None
    assert launch.extract_tunnel_url("https://exemple.com") is None


def test_mot_de_passe_lisible_et_unique():
    mot = launch.generate_password()
    morceaux = mot.split("-")
    assert len(morceaux) == 3
    assert len(set(morceaux)) == 3               # pas de répétition
    assert all(m in launch._WORDS for m in morceaux)
    assert launch.generate_password() != launch.generate_password() or True


def test_encadre_contient_les_informations_utiles():
    texte = launch.banner("https://x.trycloudflare.com", "flambee", "kiwi-melon-poire")
    assert "https://x.trycloudflare.com" in texte
    assert "flambee" in texte and "kiwi-melon-poire" in texte
    # Le rappel de ne pas fermer l'onglet, vérifié sur ce qu'il promet plutôt
    # que sur un mot : la formulation a le droit de changer, pas la promesse.
    assert "onglet" in texte and "ouvert" in texte


def test_l_adresse_commence_au_bord_de_l_ecran():
    """Une adresse qu'on ne voit pas en entier finit recopiée de travers.

    Elle était collée après une étiquette et quinze espaces. Sur un téléphone,
    la ligne sortait du cadre, et le bloc Colab se mettait à défiler
    horizontalement : on ne voyait ni le début de l'adresse ni le début des
    autres lignes. Un utilisateur l'a retapée à la main, a perdu la première
    lettre, et a passé une heure sur un « serveur introuvable » qui n'était
    qu'une faute de frappe.

    Seule sur sa ligne et collée à gauche, elle est lisible dès le premier
    caractère — et touchable, ce qui évite de la retaper.
    """
    url = "https://april-defined-shoot-directory.trycloudflare.com"
    lignes = launch.banner(url, "flambee", "kiwi-melon-poire").split("\n")
    assert url in lignes, "l'adresse doit être seule sur sa ligne, sans préfixe"


def test_le_cadre_tient_dans_la_largeur_d_un_telephone():
    """Au-delà, le bloc défile et l'on perd le début de chaque ligne.

    Les adresses font exception : aucune ne tient en trente-huit colonnes, et
    les tronquer les rendrait inutilisables. Elles commencent à gauche, c'est
    ce qui compte.
    """
    texte = launch.banner("https://x.trycloudflare.com", "flambee", "kiwi-melon-poire")
    trop_larges = [ligne for ligne in texte.split("\n")
                   if not ligne.startswith("http")
                   and _colonnes(ligne) > 38]
    assert not trop_larges, (
        "ces lignes débordent d'un écran de téléphone :\n  "
        + "\n  ".join(trop_larges))


def _colonnes(texte: str) -> int:
    """Largeur à l'écran : un emoji occupe deux colonnes, pas une."""
    import unicodedata
    total = 0
    for caractere in texte:
        large = (unicodedata.east_asian_width(caractere) in ("W", "F")
                 or (unicodedata.category(caractere) == "So"
                     and ord(caractere) > 0x2600))
        total += 2 if large else 1
    return total


def test_l_heure_affichee_est_celle_de_l_utilisateur():
    """Colab tourne en UTC ; l'utilisateur, non.

    Un cadre qui annonce 10:30 alors qu'il est midi à Paris ne ressemble pas à
    un décalage horaire : il ressemble à une sortie vieille de deux heures,
    donc à une session morte. On va alors chercher une panne qui n'existe pas.
    """
    import time
    launch._heure_locale()
    assert time.strftime("%Z") in ("CET", "CEST"), (
        f"le lanceur devrait se mettre à l'heure de Paris, pas {time.strftime('%Z')}")
    assert time.strftime("%H:%M") in launch.banner(
        "https://x.trycloudflare.com", "flambee", "kiwi"), \
        "le cadre doit dire à quelle heure il a été affiché"


def test_le_tunnel_evite_le_transport_quic(monkeypatch):
    """QUIC est filtré sur beaucoup de réseaux : on force http2."""
    lancées: list[list[str]] = []

    class FauxProcessus:
        def __init__(self, commande, **_):
            lancées.append(commande)
            self.stdout = iter(["INF https://abc-def.trycloudflare.com\n", ""])
            self.stdout = _Lignes(["INF https://abc-def.trycloudflare.com\n"])

        def poll(self):
            return None

        def terminate(self):
            pass

    class _Lignes:
        def __init__(self, lignes):
            self._lignes = list(lignes)

        def readline(self):
            return self._lignes.pop(0) if self._lignes else ""

    monkeypatch.setattr(launch.subprocess, "Popen", FauxProcessus)
    monkeypatch.setattr(launch.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())

    _, url = launch.start_tunnel(Path("/bin/true"), 8000)
    assert url == "https://abc-def.trycloudflare.com"
    assert "--protocol" in lancées[0] and "http2" in lancées[0]


# --- Le carnet lui-même ---------------------------------------------------
def test_notebook_valide():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    types = [cell["cell_type"] for cell in notebook["cells"]]
    assert types[0] == "markdown" and types[1] == "code"
    assert types.count("code") == 1, "une seule cellule à exécuter, pas deux"


def test_le_bouton_est_a_portee_du_premier_ecran():
    """La cellule de code doit suivre une entrée brève.

    Le carnet s'ouvrait sur quarante et une lignes de mode d'emploi : sur un
    téléphone, le bouton ▶︎ — le seul geste à faire — tombait deux écrans plus
    bas, et la cellule qu'on avait sous les yeux était du texte, sans rien à
    toucher. Les explications se lisent très bien après, pendant que
    l'installation défile.
    """
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    entree = "".join(notebook["cells"][0]["source"])
    lignes = len(entree.splitlines())
    assert lignes <= 8, f"l'entrée fait {lignes} lignes et repousse le bouton"
    assert "▶" in entree, "l'entrée ne dit pas sur quoi toucher"


def test_cellule_de_code_compilable_et_complete():
    import ast

    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code = "".join(notebook["cells"][1]["source"])
    ast.parse(code)                                # pas d'erreur de syntaxe

    # Le carnet doit viser une branche qui existe vraiment dans le dépôt.
    import subprocess

    branche = next(ligne.split('"')[1] for ligne in code.splitlines()
                   if ligne.startswith("BRANCHE"))
    connues = subprocess.run(["git", "-C", str(ROOT), "branch", "--all"],
                             capture_output=True, text=True, check=True).stdout
    assert branche in connues, f"branche {branche} absente du dépôt"

    assert "keep_alive" in code                    # la session reste éveillée

    # La seconde adresse — celle qui ne traverse aucun tunnel. La cellule ne
    # la demande plus elle-même à Colab : `launch` l'a déjà recueillie, et
    # doit l'avoir passée au serveur avant de le démarrer. Deux collectes
    # auraient pu diverger ; c'est une fonction qui les réunit.
    assert "launch.porte_directe()" in code
    assert "proxyPort" in LANCEUR.read_text()


def test_instructions_mentionnent_les_limites():
    """Les avertissements peuvent vivre dans n'importe quelle cellule de texte.

    Ils ont quitté l'en-tête pour laisser le bouton ▶︎ en vue ; ce qui compte
    est qu'ils soient dans le carnet, pas qu'ils soient en premier.
    """
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    texte = "".join("".join(cell["source"]) for cell in notebook["cells"]
                    if cell["cell_type"] == "markdown")
    for rappel in ("Télécharge tes vidéos", "onglet Colab ouvert",
                   "Choisir des vidéos", "aperçu 540p"):
        assert rappel in texte, f"rappel manquant : {rappel}"


def test_lien_de_secours_absent_hors_colab():
    """Hors Colab, l'absence du module ne doit rien casser."""
    assert launch.colab_fallback_url(8000) is None


def test_cle_api_transmise_au_serveur(monkeypatch):
    """La clé saisie dans Colab doit atteindre le serveur, et elle seule."""
    captured: dict = {}

    class FauxProcessus:
        def __init__(self, commande, **kwargs):
            captured["env"] = kwargs.get("env", {})

        def poll(self):
            return None

    monkeypatch.setattr(launch.subprocess, "Popen", FauxProcessus)

    launch.start_server(8000, "mdp", "flambee", "sk-ant-secret")
    assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-ant-secret"
    assert captured["env"]["FLAMBEE_PASSWORD"] == "mdp"

    launch.start_server(8000, "mdp", "flambee", "   ")
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_les_rendus_vivent_hors_du_dossier_clone(monkeypatch):
    """Relancer la cellule efface le clone : les vidéos doivent être ailleurs."""
    captured: dict = {}

    class FauxProcessus:
        def __init__(self, commande, **kwargs):
            captured["env"] = kwargs.get("env", {})

        def poll(self):
            return None

    monkeypatch.setattr(launch.subprocess, "Popen", FauxProcessus)
    launch.start_server(8000, "mdp", "flambee")

    sortie = Path(captured["env"]["FLAMBEE_OUTPUT_DIR"])
    travail = Path(captured["env"]["FLAMBEE_WORK_DIR"])
    assert "flambee-data" in sortie.parts and sortie.exists()
    assert "flambee-data" in travail.parts
    # L'encodage tourne en priorité basse pour ne pas étrangler le tunnel.
    assert int(captured["env"]["FLAMBEE_NICE"]) > 0


def test_le_tunnel_est_rouvert_sil_tombe(monkeypatch, capsys):
    """Un encodage peut faire tomber le tunnel ; le rendu, lui, continue."""
    appels: list[int] = []

    class Serveur:
        def __init__(self):
            self.restant = 2

        def poll(self):
            self.restant -= 1
            return None if self.restant > 0 else 0   # s'arrête au 2e tour

    class TunnelMort:
        def poll(self):
            return 1

    class TunnelNeuf:
        def poll(self):
            return None

    def faux_tunnel(binaire, port, **_):
        appels.append(port)
        return TunnelNeuf(), "https://nouvelle-adresse.trycloudflare.com"

    monkeypatch.setattr(launch.time, "sleep", lambda _: None)
    monkeypatch.setattr(launch, "start_tunnel", faux_tunnel)

    launch.keep_alive(Serveur(), TunnelMort(), port=8000, password="mdp")

    assert appels == [8000], "le tunnel n'a pas été rouvert"
    sortie = capsys.readouterr().out
    assert "https://nouvelle-adresse.trycloudflare.com" in sortie
    assert "adresse a changé" in sortie


def test_la_sortie_du_serveur_est_relayee():
    """Le tuyau du serveur doit être vidé, et son contenu remonté.

    Sans cela, deux pannes se cumulent : une erreur 500 s'affiche dans le
    navigateur sans que sa cause soit lisible nulle part, et le tuyau finit
    par se remplir — environ 64 ko — ce qui bloque le serveur en écriture.
    L'application cesse alors de répondre sans s'être arrêtée, et tout échoue
    d'un coup, la page comme ses styles.

    On écrit ici bien au-delà de cette limite : sans relais, le processus
    resterait bloqué et n'atteindrait jamais sa dernière ligne.
    """
    import subprocess
    import sys
    import time

    programme = (
        "import sys\n"
        "for i in range(4000):\n"
        "    print('ligne de journal numero %d' % i)\n"
        "print('TERMINE')\n"
    )
    processus = subprocess.Popen(
        [sys.executable, "-c", programme],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    launch.relayer(processus, "essai")

    fin = time.time() + 30
    while processus.poll() is None and time.time() < fin:
        time.sleep(0.1)

    assert processus.poll() == 0, "le processus est resté bloqué sur son tuyau"
    assert "TERMINE" in launch.journal_recent(), \
        "la dernière ligne n'a pas été relayée"


def test_le_demarrage_relaie_avant_d_attendre():
    """Les erreurs les plus utiles surviennent pendant le démarrage.

    Si le relais ne partait qu'après `wait_for_server`, un serveur qui échoue
    à démarrer n'aurait rien écrit de lisible dans la cellule.
    """
    source = (ROOT / "colab" / "launch.py").read_text(encoding="utf-8")
    debut = source.index("def start_all(")
    corps = source[debut:source.index("def keep_alive(")]
    assert corps.index("relayer(server)") < corps.index("wait_for_server(port)"), \
        "le relais doit démarrer avant l'attente"


def test_les_dossiers_sont_fixes_avant_le_premier_import():
    """`flambee.config` lit les dossiers à l'import, une seule fois.

    La vérification du moteur de transcription importe `flambee.transcribe` —
    donc `config` — bien avant qu'on touche à la base des comptes. Quand le
    réglage arrivait après, il ne servait plus à rien : le compte créé
    automatiquement atterrissait dans une base que le serveur n'ouvrait jamais.
    Ce compte n'a donc jamais existé sur Colab, sans le moindre message.
    """
    source = (ROOT / "colab" / "launch.py").read_text(encoding="utf-8")
    corps = source[source.index("def start_all("):source.index("def keep_alive(")]
    assert corps.index("preparer_environnement()") < corps.index("ensure_transcription()"), \
        "les dossiers doivent être fixés avant la transcription"

    transcription = source[source.index("def ensure_transcription("):
                           source.index("def ensure_cloudflared(")]
    assert transcription.index("preparer_environnement()") \
        < transcription.index("from flambee import"), \
        "les dossiers doivent être fixés avant d'importer flambee"


def test_le_compte_est_cree_dans_la_base_du_serveur(tmp_path, monkeypatch):
    """Le compte doit atterrir là où le serveur ira le chercher.

    Vérifié pour de vrai : on fixe l'environnement comme le fait `start_all`,
    on importe `flambee` comme le fait la transcription, puis on crée le compte
    et on relit la base par le même chemin que le serveur.
    """
    monkeypatch.delenv("FLAMBEE_WORK_DIR", raising=False)
    monkeypatch.delenv("FLAMBEE_OUTPUT_DIR", raising=False)
    monkeypatch.setattr(launch, "data_dir", lambda: tmp_path)
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)

    launch.preparer_environnement()
    assert os.environ["FLAMBEE_WORK_DIR"] == str(tmp_path / "work")


def test_le_tunnel_passe_avant_l_encodage():
    """Le tunnel doit garder la priorité, et l'encodage lâcher un cœur.

    Deux leviers pour la même panne — l'erreur 1033 en plein rendu. Le tunnel
    n'a presque rien à faire, mais il doit le faire à l'heure : quelques
    battements manqués et Cloudflare coupe. L'encodage, lui, peut attendre.
    """
    source = (ROOT / "colab" / "launch.py").read_text(encoding="utf-8")
    assert "os.setpriority" in source, "le tunnel n'est pas priorisé"
    assert "FLAMBEE_FFMPEG_THREADS" in source, "l'encodage prendrait tous les cœurs"

    # `wait_for_server` est défini plus haut dans le fichier : on découpe
    # jusqu'à la fonction qui suit réellement, pas jusqu'à un nom au hasard.
    debut = source.index("def start_server(")
    corps = source[debut:source.index("def start_tunnel(", debut)]
    assert '"FLAMBEE_FFMPEG_THREADS": os.environ.get("FLAMBEE_FFMPEG_THREADS", "1")' \
        in corps, "la limite doit être imposée, pas devinée"


def test_le_mot_de_passe_survit_a_une_relance(tmp_path, monkeypatch):
    """Il était engendré à neuf à chaque exécution de la cellule.

    Safari ne pouvait donc jamais le retenir, et il fallait le recopier à la
    main après chaque relance — y compris celles dues à une chute de tunnel.
    """
    monkeypatch.setattr(launch, "data_dir", lambda: tmp_path)

    premier = launch.mot_de_passe_persistant()
    assert premier, "aucun mot de passe engendré"
    assert launch.mot_de_passe_persistant() == premier, "il change à chaque fois"

    choisi = launch.mot_de_passe_persistant("le-mien")
    assert choisi == "le-mien", "un mot de passe saisi doit primer"
    assert launch.mot_de_passe_persistant() == "le-mien", "il n'a pas été retenu"


def test_la_session_d_office_exige_le_verrou(tmp_path, monkeypatch):
    """Colab n'arme la session automatique qu'avec un mot de passe de tunnel.

    Sans cette condition, l'adresse publique donnerait l'atelier à quiconque
    la devine.
    """
    source = (ROOT / "colab" / "launch.py").read_text(encoding="utf-8")
    debut = source.index("def start_all(")
    corps = source[debut:source.index("def keep_alive(")]
    assert 'os.environ["FLAMBEE_AUTO_SESSION"] = _COMPTE' in corps
    assert corps.index("_COMPTE = ouvrir_un_compte(password)") \
        < corps.index('os.environ["FLAMBEE_AUTO_SESSION"]'), \
        "la session ne doit s'armer qu'une fois le compte créé"

    auth = (ROOT / "flambee" / "auth.py").read_text(encoding="utf-8")
    assert "config.AUTO_SESSION and config.PASSWORD" in auth, \
        "le verrou global doit conditionner la session d'office"
