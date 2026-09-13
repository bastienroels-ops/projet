"""Comptes utilisateurs : stockage, mots de passe, sessions.

Trois choix structurants, tous tenables pour un service qui démarre :

- **SQLite** plutôt que des fichiers JSON. Les comptes se lisent et s'écrivent
  en concurrence dès le deuxième visiteur ; une base transactionnelle évite les
  écritures entrelacées. Aucun serveur à installer.
- **scrypt** pour les mots de passe, depuis la bibliothèque standard. Pas de
  dépendance à surveiller, et une fonction de dérivation reconnue.
- **Cookies signés** plutôt qu'une table de sessions. La signature HMAC suffit
  à garantir l'intégrité, et rien à purger côté serveur.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_local = threading.local()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")
MIN_PASSWORD = 8
# Le premier compte créé est celui de la personne qui installe Flambée : elle
# administre le service, elle n'est pas sa propre cliente. Elle reçoit donc la
# formule la plus complète, sans quoi elle devrait contourner à la main, à
# chaque nouvelle installation, un verrou qu'elle a elle-même posé.
PLAN_PROPRIETAIRE = os.environ.get("FLAMBEE_PLAN_PROPRIETAIRE", "studio").strip()

SESSION_COOKIE = "flambee_session"
SESSION_DAYS = 30

# Paramètres scrypt : coût mémoire ~16 Mo, quelques dizaines de ms par essai.
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class CompteError(RuntimeError):
    """Création ou connexion refusée."""


@dataclass
class Utilisateur:
    id: int
    email: str
    nom: str = ""
    plan: str = "essai"
    cree_le: str = ""
    historique: list[dict] = field(default_factory=list)

    @property
    def initiales(self) -> str:
        source = (self.nom or self.email).strip()
        morceaux = [m for m in source.replace("@", " ").replace(".", " ").split() if m]
        if not morceaux:
            return "FL"
        if len(morceaux) == 1:
            return morceaux[0][:2].upper()
        return (morceaux[0][0] + morceaux[1][0]).upper()

    @property
    def dossier(self) -> Path:
        """Chaque compte a son propre espace : projets, voix, rendus."""
        chemin = config.WORK_DIR / "utilisateurs" / str(self.id)
        chemin.mkdir(parents=True, exist_ok=True)
        return chemin


# --- Base de données ------------------------------------------------------
def chemin_base() -> Path:
    return config.WORK_DIR / "flambee.db"


def connexion() -> sqlite3.Connection:
    """Une connexion par fil d'exécution (SQLite l'exige)."""
    base = getattr(_local, "db", None)
    if base is not None:
        return base
    chemin_base().parent.mkdir(parents=True, exist_ok=True)
    base = sqlite3.connect(chemin_base(), timeout=15, check_same_thread=False)
    base.row_factory = sqlite3.Row
    base.execute("PRAGMA journal_mode=WAL")     # lectures concurrentes
    base.execute("PRAGMA foreign_keys=ON")
    _local.db = base
    _creer_tables(base)
    return base


def _creer_tables(base: sqlite3.Connection) -> None:
    base.executescript("""
        CREATE TABLE IF NOT EXISTS utilisateurs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            mot_de_passe TEXT   NOT NULL,
            nom         TEXT    NOT NULL DEFAULT '',
            plan        TEXT    NOT NULL DEFAULT 'essai',
            cree_le     TEXT    NOT NULL,
            historique  TEXT    NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_email ON utilisateurs(email);
    """)
    base.commit()


# --- Mots de passe --------------------------------------------------------
def hacher(mot_de_passe: str) -> str:
    sel = secrets.token_bytes(16)
    empreinte = hashlib.scrypt(mot_de_passe.encode("utf-8"), salt=sel, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}$" \
           f"{sel.hex()}${empreinte.hex()}"


def verifier(mot_de_passe: str, stocke: str) -> bool:
    try:
        algo, n, r, p, sel, attendu = stocke.split("$")
        if algo != "scrypt":
            return False
        empreinte = hashlib.scrypt(
            mot_de_passe.encode("utf-8"), salt=bytes.fromhex(sel),
            n=int(n), r=int(r), p=int(p), dklen=len(attendu) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(empreinte.hex(), attendu)


# --- Validation -----------------------------------------------------------
def valider_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise CompteError("Cette adresse e-mail ne semble pas valide.")
    return email


def valider_mot_de_passe(mot_de_passe: str) -> str:
    if len(mot_de_passe) < MIN_PASSWORD:
        raise CompteError(
            f"Le mot de passe doit faire au moins {MIN_PASSWORD} caractères."
        )
    if len(mot_de_passe) > 1024:            # borne les calculs de scrypt
        raise CompteError("Ce mot de passe est trop long.")
    return mot_de_passe


# --- Comptes --------------------------------------------------------------
def inscriptions_ouvertes() -> bool:
    return os.environ.get("FLAMBEE_SIGNUP", "ouvert").strip().lower() != "ferme"


def code_invitation() -> str:
    return os.environ.get("FLAMBEE_INVITE_CODE", "").strip()


def creer(email: str, mot_de_passe: str, nom: str = "",
          invitation: str = "") -> Utilisateur:
    """Crée un compte. Lève CompteError si quelque chose s'y oppose."""
    email = valider_email(email)
    valider_mot_de_passe(mot_de_passe)

    attendu = code_invitation()
    if attendu and not hmac.compare_digest(invitation.strip(), attendu):
        raise CompteError("Code d'invitation invalide.")
    if not inscriptions_ouvertes() and compter() > 0:
        raise CompteError("Les inscriptions sont fermées pour le moment.")

    with _LOCK:
        base = connexion()
        # Le décompte est lu sous le verrou : deux inscriptions simultanées sur
        # une base vide ne peuvent pas se croire toutes deux « la première ».
        plan = PLAN_PROPRIETAIRE if compter() == 0 else "essai"
        try:
            curseur = base.execute(
                "INSERT INTO utilisateurs (email, mot_de_passe, nom, plan, cree_le) "
                "VALUES (?, ?, ?, ?, ?)",
                (email, hacher(mot_de_passe), nom.strip()[:80], plan,
                 time.strftime("%Y-%m-%d")),
            )
            base.commit()
        except sqlite3.IntegrityError as exc:
            raise CompteError("Un compte existe déjà avec cette adresse.") from exc

    if plan != "essai":
        log.info("Compte créé : %s — administrateur, formule %s", email, plan)
    else:
        log.info("Compte créé : %s", email)
    return par_id(curseur.lastrowid)        # type: ignore[arg-type]


def authentifier(email: str, mot_de_passe: str) -> Utilisateur:
    ligne = connexion().execute(
        "SELECT * FROM utilisateurs WHERE email = ?", (email.strip().lower(),)
    ).fetchone()

    # On calcule une empreinte même sans compte : sans cela, le temps de
    # réponse révélerait quelles adresses sont enregistrées.
    reference = ligne["mot_de_passe"] if ligne else hacher("_")
    valide = verifier(mot_de_passe, reference)
    if not ligne or not valide:
        raise CompteError("Adresse ou mot de passe incorrect.")
    return _depuis_ligne(ligne)


def par_id(identifiant: int) -> Utilisateur | None:
    ligne = connexion().execute(
        "SELECT * FROM utilisateurs WHERE id = ?", (identifiant,)
    ).fetchone()
    return _depuis_ligne(ligne) if ligne else None


def par_email(email: str) -> Utilisateur | None:
    ligne = connexion().execute(
        "SELECT * FROM utilisateurs WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    return _depuis_ligne(ligne) if ligne else None


def compter() -> int:
    return connexion().execute("SELECT COUNT(*) AS n FROM utilisateurs").fetchone()["n"]


def _depuis_ligne(ligne: sqlite3.Row) -> Utilisateur:
    try:
        historique = json.loads(ligne["historique"])
    except (json.JSONDecodeError, TypeError):
        historique = []
    return Utilisateur(
        id=ligne["id"], email=ligne["email"], nom=ligne["nom"],
        plan=ligne["plan"], cree_le=ligne["cree_le"], historique=historique,
    )


def mettre_a_jour(identifiant: int, **champs) -> Utilisateur | None:
    autorises = {"nom", "plan", "email"}
    modifs = {k: v for k, v in champs.items() if k in autorises}
    if not modifs:
        return par_id(identifiant)
    with _LOCK:
        base = connexion()
        assignations = ", ".join(f"{cle} = ?" for cle in modifs)
        base.execute(f"UPDATE utilisateurs SET {assignations} WHERE id = ?",
                     (*modifs.values(), identifiant))
        base.commit()
    return par_id(identifiant)


def changer_mot_de_passe(identifiant: int, ancien: str, nouveau: str) -> None:
    ligne = connexion().execute(
        "SELECT mot_de_passe FROM utilisateurs WHERE id = ?", (identifiant,)
    ).fetchone()
    if not ligne or not verifier(ancien, ligne["mot_de_passe"]):
        raise CompteError("Mot de passe actuel incorrect.")
    valider_mot_de_passe(nouveau)
    with _LOCK:
        base = connexion()
        base.execute("UPDATE utilisateurs SET mot_de_passe = ? WHERE id = ?",
                     (hacher(nouveau), identifiant))
        base.commit()


def noter(identifiant: int, type_: str, detail: str = "") -> None:
    """Ajoute un évènement à l'historique du compte (crédits, transcriptions…)."""
    with _LOCK:
        base = connexion()
        ligne = base.execute("SELECT historique FROM utilisateurs WHERE id = ?",
                             (identifiant,)).fetchone()
        if not ligne:
            return
        try:
            historique = json.loads(ligne["historique"])
        except (json.JSONDecodeError, TypeError):
            historique = []
        historique.append({
            "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": type_, "detail": detail[:160],
        })
        base.execute("UPDATE utilisateurs SET historique = ? WHERE id = ?",
                     (json.dumps(historique[-500:], ensure_ascii=False), identifiant))
        base.commit()


def supprimer(identifiant: int) -> None:
    """Efface le compte et tout son espace de travail (droit à l'effacement)."""
    import shutil

    utilisateur = par_id(identifiant)
    with _LOCK:
        base = connexion()
        base.execute("DELETE FROM utilisateurs WHERE id = ?", (identifiant,))
        base.commit()
    if utilisateur:
        shutil.rmtree(utilisateur.dossier, ignore_errors=True)


# --- Réinitialisation du mot de passe -------------------------------------
# Le jeton est signé comme une session, mais porte un marqueur distinct : un
# lien de réinitialisation ne peut donc pas servir de cookie de session, ni
# l'inverse. Durée volontairement courte.
REINIT_MINUTES = 60


def creer_jeton_reinit(identifiant: int) -> str:
    expiration = int(time.time()) + REINIT_MINUTES * 60
    empreinte = _empreinte_mot_de_passe(identifiant)
    charge = f"reinit.{identifiant}.{expiration}.{empreinte}".encode("utf-8")
    signature = hmac.new(_secret(), charge, hashlib.sha256).digest()
    return (base64.urlsafe_b64encode(charge).decode().rstrip("=") + "."
            + base64.urlsafe_b64encode(signature).decode().rstrip("="))


def lire_jeton_reinit(jeton: str) -> int | None:
    """Valide un jeton de réinitialisation. Un lien ne sert qu'une fois :
    l'empreinte du mot de passe actuel en fait partie, donc le changer
    invalide tous les liens émis."""
    try:
        partie_charge, partie_signature = jeton.split(".", 1)
        charge = base64.urlsafe_b64decode(
            partie_charge + "=" * (-len(partie_charge) % 4))
        signature = base64.urlsafe_b64decode(
            partie_signature + "=" * (-len(partie_signature) % 4))
    except (ValueError, TypeError):
        return None

    if not hmac.compare_digest(
            signature, hmac.new(_secret(), charge, hashlib.sha256).digest()):
        return None
    try:
        marqueur, identifiant, expiration, empreinte = charge.decode("utf-8").split(".")
    except ValueError:
        return None
    if marqueur != "reinit" or int(expiration) < time.time():
        return None
    if not hmac.compare_digest(empreinte, _empreinte_mot_de_passe(int(identifiant))):
        return None
    return int(identifiant)


def _empreinte_mot_de_passe(identifiant: int) -> str:
    """Empreinte courte du mot de passe actuel, pour rendre un lien à usage unique."""
    ligne = connexion().execute(
        "SELECT mot_de_passe FROM utilisateurs WHERE id = ?", (identifiant,)
    ).fetchone()
    if not ligne:
        return ""
    return hashlib.sha256(ligne["mot_de_passe"].encode("utf-8")).hexdigest()[:16]


def definir_mot_de_passe(identifiant: int, nouveau: str) -> None:
    """Pose un nouveau mot de passe sans demander l'ancien (réinitialisation)."""
    valider_mot_de_passe(nouveau)
    with _LOCK:
        base = connexion()
        base.execute("UPDATE utilisateurs SET mot_de_passe = ? WHERE id = ?",
                     (hacher(nouveau), identifiant))
        base.commit()


# --- Sessions signées -----------------------------------------------------
def _secret() -> bytes:
    """Clé de signature : depuis l'environnement, sinon engendrée et conservée.

    Sans persistance, chaque redémarrage déconnecterait tout le monde.
    """
    depuis_env = os.environ.get("FLAMBEE_SECRET_KEY", "").strip()
    if depuis_env:
        return depuis_env.encode("utf-8")

    fichier = config.WORK_DIR / ".secret"
    if fichier.exists():
        return fichier.read_bytes()
    with _LOCK:
        if fichier.exists():
            return fichier.read_bytes()
        secret = secrets.token_bytes(32)
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(secret)
        try:
            fichier.chmod(0o600)
        except OSError:                      # systèmes sans permissions POSIX
            pass
        return secret


def creer_session(identifiant: int, duree_jours: int = SESSION_DAYS) -> str:
    expiration = int(time.time()) + duree_jours * 86400
    charge = f"{identifiant}.{expiration}".encode("utf-8")
    signature = hmac.new(_secret(), charge, hashlib.sha256).digest()
    return (base64.urlsafe_b64encode(charge).decode().rstrip("=") + "."
            + base64.urlsafe_b64encode(signature).decode().rstrip("="))


def lire_session(jeton: str) -> int | None:
    """Retourne l'identifiant si le jeton est valide et non expiré."""
    try:
        partie_charge, partie_signature = jeton.split(".", 1)
        charge = base64.urlsafe_b64decode(partie_charge + "=" * (-len(partie_charge) % 4))
        signature = base64.urlsafe_b64decode(
            partie_signature + "=" * (-len(partie_signature) % 4))
    except (ValueError, TypeError):
        return None

    attendue = hmac.new(_secret(), charge, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, attendue):
        return None
    try:
        identifiant, expiration = charge.decode("utf-8").split(".")
        if int(expiration) < time.time():
            return None
        return int(identifiant)
    except ValueError:
        return None
