"""Trend Finder — repérer, analyser et s'inspirer de contenus tendance.

Aucune recherche en direct sur TikTok n'est branchée : il n'existe pas
aujourd'hui d'API tierce gratuite et légale pour interroger TikTok par
mots-clés (voir `docs/TENDANCES.md` pour le détail des chemins possibles).
Ce module part donc de vidéos que l'utilisateur repère lui-même — une
recherche manuelle sur TikTok, un usage normal du site — et colle ici :
`yt-dlp` en récupère les vraies métadonnées publiques (vues, likes,
commentaires, partages, durée, hashtags, date), exactement la technique que
`downloader.py` utilise déjà pour le montage. Aucune page TikTok n'est
grattée, aucune protection contournée.

Trois principes stricts, demandés explicitement et tenus ici :

- **jamais de donnée inventée** : un champ que la plateforme n'a pas renvoyé
  reste `None`, jamais estimé ni approché ;
- **données brutes et analyses calculées toujours distinguées** : chaque
  objet sérialisé sépare `donnees` (ce que yt-dlp a renvoyé tel quel) et
  `analyse` (ce que Flambée en déduit — score, rythme, structure…) ;
- **prêt pour un vrai moteur de recherche** : le jour où une clé d'API
  légale existe (TikTok Research API, ou un fournisseur de données tiers
  sous licence), il se branche ici sans toucher au reste de l'application.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import analyzer, downloader, transcribe, users
from .media import MediaError

log = logging.getLogger(__name__)

_LOCK = threading.RLock()

_HASHTAG_RE = re.compile(r"#(\w[\w']*)", re.UNICODE)

# Au-delà, une recherche devient un vrai job de fond plutôt qu'une réponse
# HTTP synchrone — inutile tant que le volume reste celui d'une recherche
# manuelle (quelques dizaines de liens repérés à la main).
MAX_LIENS_RECHERCHE = 30

TYPES_SAUVEGARDE = {"video", "hashtag", "recherche", "tendance"}

_FORMAT_ISO = "%Y-%m-%dT%H:%M:%S"


def _horodatage(epoch: float | None = None) -> str:
    """Horodatage ISO en UTC — toujours cette même horloge dans ce module.

    Un mélange heure locale / UTC entre l'écriture d'un relevé et la lecture
    d'un autre fausserait silencieusement chaque comparaison de date (donc
    les fenêtres de « Pépites », le filtre de période, la fréquence de
    veille) de l'écart au fuseau de la machine — invisible en développement
    UTC, faux partout ailleurs. Toute date de ce module passe par ici.
    """
    return time.strftime(_FORMAT_ISO, time.gmtime(epoch))


def _depuis_iso(iso: str) -> float | None:
    """L'inverse de `_horodatage` — `None` si la chaîne est illisible."""
    try:
        return calendar.timegm(time.strptime(iso, _FORMAT_ISO))
    except ValueError:
        return None


# --- Vidéo tendance : uniquement des données constatées --------------------
@dataclass
class VideoTendance:
    """Une vidéo, avec uniquement ce que `yt-dlp` a réellement renvoyé.

    Aucun champ n'est complété par une estimation : une valeur à `None`
    signifie que la plateforme ne l'a pas communiquée pour ce lien, et
    l'interface doit le dire plutôt que d'afficher un zéro trompeur.
    """

    url: str
    id: str = ""
    plateforme: str = ""
    titre: str = ""
    createur: str = ""
    miniature: str = ""
    vues: int | None = None
    likes: int | None = None
    commentaires: int | None = None
    partages: int | None = None
    duree: float | None = None
    publie_le: str = ""              # ISO 8601 UTC, "" si inconnu
    hashtags: list[str] = field(default_factory=list)
    erreur: str = ""

    @property
    def ok(self) -> bool:
        return not self.erreur

    # --- Analyses dérivées, jamais inventées -------------------------------
    @property
    def engagement(self) -> float | None:
        """(likes + commentaires + partages) / vues.

        `None` dès qu'un seul des compteurs nécessaires manque : mieux vaut
        ne rien afficher qu'un taux calculé sur une valeur absente.
        """
        if not self.vues:
            return None
        composantes = (self.likes, self.commentaires, self.partages)
        if any(c is None for c in composantes):
            return None
        return round(sum(composantes) / self.vues, 4)  # type: ignore[arg-type]

    @property
    def anciennete_heures(self) -> float | None:
        if not self.publie_le:
            return None
        epoque = _depuis_iso(self.publie_le)
        if epoque is None:
            return None
        return max(0.0, (time.time() - epoque) / 3600)

    @property
    def vitesse_vues_heure(self) -> float | None:
        """Vues par heure depuis la publication : un indice de rapidité de
        décollage, pas une prédiction. `None` si les vues ou la date de
        publication manquent, ou si la vidéo a moins d'une heure (la
        division exploserait sur presque rien)."""
        anciennete = self.anciennete_heures
        if anciennete is None or self.vues is None or anciennete < 1:
            return None
        return round(self.vues / anciennete, 1)

    @property
    def score_pepite(self) -> float | None:
        """Combine la vitesse de vues et l'engagement pour repérer un
        décollage rapide plutôt qu'un simple gros total. `None` si la
        vitesse n'est pas calculable ; l'engagement, quand il manque, ne
        pèse simplement pas dans le score au lieu de l'annuler."""
        vitesse = self.vitesse_vues_heure
        if vitesse is None:
            return None
        return round(vitesse * (1 + (self.engagement or 0) * 8), 1)

    def to_dict(self) -> dict:
        brut = asdict(self)
        erreur = brut.pop("erreur")
        return {
            "donnees": brut,
            "ok": self.ok,
            "erreur": erreur,
            "analyse": {
                "engagement": self.engagement,
                "anciennete_heures": (
                    round(self.anciennete_heures, 1)
                    if self.anciennete_heures is not None else None
                ),
                "vitesse_vues_heure": self.vitesse_vues_heure,
                "score_pepite": self.score_pepite,
            },
        }


def _normaliser_hashtags(texte: str) -> list[str]:
    vus: set[str] = set()
    hashtags: list[str] = []
    for correspondance in _HASHTAG_RE.findall(texte or ""):
        tag = correspondance.lower()
        if tag not in vus:
            vus.add(tag)
            hashtags.append(tag)
    return hashtags


def _depuis_info(url: str, info: dict) -> VideoTendance:
    horodatage = info.get("timestamp") or info.get("release_timestamp")
    publie = _horodatage(horodatage) if horodatage else ""
    description = (info.get("description") or info.get("title") or "").strip()
    hashtags = _normaliser_hashtags(description)
    for brut in info.get("tags") or []:
        tag = str(brut).lstrip("#").lower()
        if tag and tag not in hashtags:
            hashtags.append(tag)
    adresse = info.get("webpage_url") or url
    premiere_ligne = description.splitlines()[0] if description else ""
    return VideoTendance(
        url=adresse,
        id=hashlib.sha256(adresse.encode("utf-8")).hexdigest()[:16],
        plateforme=info.get("extractor_key") or "",
        titre=premiere_ligne[:220],
        createur=info.get("uploader") or info.get("channel") or "",
        miniature=info.get("thumbnail") or "",
        vues=info.get("view_count"),
        likes=info.get("like_count"),
        commentaires=info.get("comment_count"),
        # `or` perdrait un vrai zéro (0 partage est fréquent et légitime) :
        # on ne retombe sur `share_count` que si `repost_count` est absent.
        partages=(info["repost_count"] if info.get("repost_count") is not None
                 else info.get("share_count")),
        duree=float(info["duration"]) if info.get("duration") else None,
        publie_le=publie,
        hashtags=hashtags,
    )


def fetch_metadata(url: str) -> VideoTendance:
    """Métadonnées publiques d'une vidéo, sans télécharger le fichier.

    Même technique que `downloader.py` (yt-dlp, appel public), sans écriture
    sur disque : le Trend Finder n'a besoin que des compteurs et de la
    description, pas du fichier vidéo lui-même.
    """
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError as YdlError

    options = {
        **downloader._cookie_options(),
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                entrees = [e for e in info.get("entries") or [] if e]
                if not entrees:
                    return VideoTendance(
                        url=url, erreur="Aucune vidéo trouvée derrière ce lien.")
                info = entrees[0]
    except YdlError as exc:
        return VideoTendance(url=url, erreur=downloader._clean_ydl_error(str(exc)))
    except Exception as exc:  # pragma: no cover — dépend du site distant
        return VideoTendance(url=url, erreur=f"Récupération impossible : {exc}")
    return _depuis_info(url, info)


def fetch_all(urls: list[str], *, parallel: bool = True) -> list[VideoTendance]:
    """Récupère les métadonnées de plusieurs liens, en parallèle.

    Chaque lien passe l'essentiel de son temps à attendre le réseau : les
    lancer ensemble divise l'attente par le nombre de liens, comme
    `downloader.download_all`.
    """
    urls = urls[:MAX_LIENS_RECHERCHE]
    if not urls:
        return []
    if parallel and len(urls) > 1:
        with ThreadPoolExecutor(max_workers=min(len(urls), 4)) as pool:
            return list(pool.map(fetch_metadata, urls))
    return [fetch_metadata(u) for u in urls]


def periode_vers_date(periode: str) -> str:
    """Traduit un préréglage de période (7j/30j/3m/2026) en date ISO de
    départ (UTC). Chaîne vide si le préréglage n'en restreint aucune."""
    jours = {"7j": 7, "30j": 30, "3m": 90}.get(periode)
    if jours:
        return _horodatage(time.time() - jours * 86400)
    if periode == "2026":
        return "2026-01-01T00:00:00"
    return ""


def appliquer_filtres(
    videos: list[VideoTendance], *,
    mots_cles: str = "", hashtags: list[str] | None = None,
    vues_min: int | None = None, depuis_le: str = "", jusqu_au: str = "",
) -> list[VideoTendance]:
    """Filtre des vidéos déjà récupérées — jamais une requête à TikTok.

    Les échecs de récupération restent dans le résultat (l'interface doit
    pouvoir dire quel lien a échoué et pourquoi), les filtres ne s'appliquent
    qu'aux vidéos effectivement obtenues.
    """
    mots = [m.lower() for m in re.split(r"\s+", mots_cles.strip()) if m]
    tags_filtre = {h.lstrip("#").strip().lower() for h in (hashtags or []) if h.strip()}

    resultat = []
    for video in videos:
        if not video.ok:
            resultat.append(video)
            continue
        texte = f"{video.titre} {video.createur}".lower()
        if mots and not all(m in texte for m in mots):
            continue
        if tags_filtre and not (tags_filtre & set(video.hashtags)):
            continue
        if vues_min is not None and (video.vues is None or video.vues < vues_min):
            continue
        if depuis_le and (not video.publie_le or video.publie_le < depuis_le):
            continue
        if jusqu_au and video.publie_le and video.publie_le > jusqu_au:
            continue
        resultat.append(video)
    return resultat


# --- Base de données ---------------------------------------------------
# Les tendances vivent dans la même base que les comptes (`users.py`) : un
# seul fichier à sauvegarder, un seul mécanisme de connexion par fil
# d'exécution. Chaque table est cloisonnée par `utilisateur_id`, comme les
# projets le sont par dossier.
def _connexion() -> sqlite3.Connection:
    """La connexion partagée avec `users.py`, tables de tendances garanties.

    Pas de fanion « déjà créées » à retenir par fil d'exécution : sur cette
    base, `users.connexion()` peut être forcée à rouvrir une connexion neuve
    (les tests le font entre deux cas), et un fanion mis en cache manquerait
    alors que la nouvelle connexion n'a pas encore ces tables. `CREATE TABLE
    IF NOT EXISTS` est immédiat : le vérifier à chaque appel ne coûte rien.
    """
    base = users.connexion()
    _creer_tables(base)
    return base


def _creer_tables(base: sqlite3.Connection) -> None:
    base.executescript("""
        CREATE TABLE IF NOT EXISTS tendance_videos (
            id            TEXT PRIMARY KEY,
            url           TEXT NOT NULL UNIQUE,
            plateforme    TEXT NOT NULL DEFAULT '',
            titre         TEXT NOT NULL DEFAULT '',
            createur      TEXT NOT NULL DEFAULT '',
            miniature     TEXT NOT NULL DEFAULT '',
            duree         REAL,
            publie_le     TEXT NOT NULL DEFAULT '',
            hashtags      TEXT NOT NULL DEFAULT '[]',
            premiere_fois TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tendance_releves (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id       TEXT NOT NULL REFERENCES tendance_videos(id) ON DELETE CASCADE,
            utilisateur_id INTEGER NOT NULL,
            vues           INTEGER,
            likes          INTEGER,
            commentaires   INTEGER,
            partages       INTEGER,
            releve_le      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_releve_video ON tendance_releves(video_id);
        CREATE INDEX IF NOT EXISTS idx_releve_compte ON tendance_releves(utilisateur_id);

        CREATE TABLE IF NOT EXISTS tendance_sauvegardes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            utilisateur_id INTEGER NOT NULL,
            type           TEXT NOT NULL,
            reference      TEXT NOT NULL,
            libelle        TEXT NOT NULL DEFAULT '',
            donnees        TEXT NOT NULL DEFAULT '{}',
            cree_le        TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sauvegarde_compte ON tendance_sauvegardes(utilisateur_id);

        CREATE TABLE IF NOT EXISTS tendance_surveillances (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            utilisateur_id     INTEGER NOT NULL,
            libelle            TEXT NOT NULL DEFAULT '',
            requete            TEXT NOT NULL DEFAULT '{}',
            frequence_heures   INTEGER NOT NULL DEFAULT 24,
            actif              INTEGER NOT NULL DEFAULT 1,
            derniere_execution TEXT NOT NULL DEFAULT '',
            cree_le            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_veille_compte ON tendance_surveillances(utilisateur_id);
    """)
    base.commit()


def enregistrer_releve(utilisateur_id: int, video: VideoTendance) -> None:
    """Mémorise une vidéo vue et son relevé de compteurs.

    Sans historique, une « progression » ne peut être qu'inventée. Avec ces
    relevés, une vidéo revue plus tard (nouvelle recherche, ou future veille)
    donne une croissance réellement mesurée plutôt qu'une estimation.
    """
    if not video.ok or not video.id:
        return
    maintenant = _horodatage()
    with _LOCK:
        base = _connexion()
        base.execute(
            "INSERT INTO tendance_videos (id, url, plateforme, titre, createur, "
            "miniature, duree, publie_le, hashtags, premiere_fois) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET titre=excluded.titre, "
            "createur=excluded.createur, miniature=excluded.miniature",
            (video.id, video.url, video.plateforme, video.titre, video.createur,
             video.miniature, video.duree, video.publie_le,
             json.dumps(video.hashtags, ensure_ascii=False), maintenant),
        )
        base.execute(
            "INSERT INTO tendance_releves (video_id, utilisateur_id, vues, likes, "
            "commentaires, partages, releve_le) VALUES (?,?,?,?,?,?,?)",
            (video.id, utilisateur_id, video.vues, video.likes,
             video.commentaires, video.partages, maintenant),
        )
        base.commit()


def _heures_entre(debut: str, fin: str) -> float:
    t0, t1 = _depuis_iso(debut), _depuis_iso(fin)
    if t0 is None or t1 is None:
        return 0.0
    return max(0.0, (t1 - t0) / 3600)


# --- Pépites -------------------------------------------------------------
def pepites(utilisateur_id: int, *, depuis_jours: int = 60, limite: int = 20) -> list[dict]:
    """Les vidéos de ce compte qui semblent décoller vite plutôt que celles
    qui cumulent le plus de vues.

    Quand une vidéo a été relevée au moins deux fois, la croissance est
    **mesurée** (delta de vues réel entre deux relevés, par heure écoulée) ;
    sinon le score retombe sur l'estimation à un seul passage
    (`score_pepite`), clairement distinguée côté client (`mesuree` à
    `false`).
    """
    base = _connexion()
    limite_temps = _horodatage(time.time() - depuis_jours * 86400)
    lignes = base.execute(
        "SELECT v.*, r.vues AS r_vues, r.likes AS r_likes, "
        "r.commentaires AS r_commentaires, r.partages AS r_partages, "
        "r.releve_le AS r_releve_le "
        "FROM tendance_releves r JOIN tendance_videos v ON v.id = r.video_id "
        "WHERE r.utilisateur_id = ? AND r.releve_le >= ? ORDER BY r.releve_le ASC",
        (utilisateur_id, limite_temps),
    ).fetchall()

    par_video: dict[str, list[sqlite3.Row]] = {}
    for ligne in lignes:
        par_video.setdefault(ligne["id"], []).append(ligne)

    resultats = []
    for video_id, releves in par_video.items():
        premier, dernier = releves[0], releves[-1]
        video = VideoTendance(
            url=dernier["url"], id=video_id, plateforme=dernier["plateforme"],
            titre=dernier["titre"], createur=dernier["createur"],
            miniature=dernier["miniature"], vues=dernier["r_vues"],
            likes=dernier["r_likes"], commentaires=dernier["r_commentaires"],
            partages=dernier["r_partages"], duree=dernier["duree"],
            publie_le=dernier["publie_le"],
            hashtags=json.loads(dernier["hashtags"] or "[]"),
        )
        croissance_mesuree = None
        if (len(releves) >= 2 and dernier["r_vues"] is not None
                and premier["r_vues"] is not None):
            heures = _heures_entre(premier["r_releve_le"], dernier["r_releve_le"])
            if heures >= 0.5:
                croissance_mesuree = round(
                    (dernier["r_vues"] - premier["r_vues"]) / heures, 1)

        carte = video.to_dict()
        carte["analyse"]["nombre_de_releves"] = len(releves)
        carte["analyse"]["croissance_mesuree_vues_heure"] = croissance_mesuree
        carte["analyse"]["score"] = (
            croissance_mesuree if croissance_mesuree is not None
            else video.score_pepite)
        carte["analyse"]["mesuree"] = croissance_mesuree is not None
        resultats.append(carte)

    resultats.sort(
        key=lambda c: (c["analyse"]["score"] is not None, c["analyse"]["score"] or 0),
        reverse=True,
    )
    return resultats[:limite]


# --- Tendances de hashtags -------------------------------------------------
def hashtags_observes(utilisateur_id: int, *, depuis_jours: int = 60,
                      limite: int = 15) -> list[dict]:
    """Hashtags observés dans les vidéos que ce compte a récupérées.

    Ce sont des tendances **observées dans les recherches du compte**, pas un
    classement TikTok global : aucune source disponible aujourd'hui ne
    fournit ce second (voir `docs/TENDANCES.md`). Le libellé le dit toujours
    côté interface plutôt que de laisser croire à une vue d'ensemble de la
    plateforme.
    """
    base = _connexion()
    limite_temps = _horodatage(time.time() - depuis_jours * 86400)
    lignes = base.execute(
        "SELECT v.id, v.url, v.hashtags, r.vues, r.releve_le "
        "FROM tendance_releves r JOIN tendance_videos v ON v.id = r.video_id "
        "WHERE r.utilisateur_id = ? AND r.releve_le >= ? ORDER BY r.releve_le ASC",
        (utilisateur_id, limite_temps),
    ).fetchall()

    par_hashtag: dict[str, dict] = {}
    for ligne in lignes:
        for tag in json.loads(ligne["hashtags"] or "[]"):
            entree = par_hashtag.setdefault(
                tag, {"videos": {}, "premiere": ligne["releve_le"],
                      "derniere": ligne["releve_le"]})
            entree["videos"][ligne["id"]] = (ligne["url"], ligne["vues"])
            entree["derniere"] = ligne["releve_le"]

    resultats = []
    for tag, info in par_hashtag.items():
        vues = [v for _, v in info["videos"].values() if v is not None]
        exemples = [url for url, _ in list(info["videos"].values())[:3]]
        resultats.append({
            "hashtag": tag,
            "exemples": exemples,
            "analyse": {
                "nombre_de_videos_observees": len(info["videos"]),
                "vues_totales_observees": sum(vues) if vues else None,
                "vues_moyennes_observees": (
                    round(sum(vues) / len(vues)) if vues else None),
                "premiere_observation": info["premiere"],
                "derniere_observation": info["derniere"],
            },
        })
    resultats.sort(
        key=lambda h: h["analyse"]["nombre_de_videos_observees"], reverse=True)
    return resultats[:limite]


# --- Sauvegardes (#7 « Mes tendances ») -------------------------------------
def sauvegarder(utilisateur_id: int, type_: str, reference: str, *,
                libelle: str = "", donnees: dict | None = None) -> dict:
    if type_ not in TYPES_SAUVEGARDE:
        raise ValueError(f"Type de sauvegarde inconnu : {type_}")
    reference = reference.strip()
    if not reference:
        raise ValueError("Rien à sauvegarder.")
    with _LOCK:
        base = _connexion()
        curseur = base.execute(
            "INSERT INTO tendance_sauvegardes (utilisateur_id, type, reference, "
            "libelle, donnees, cree_le) VALUES (?,?,?,?,?,?)",
            (utilisateur_id, type_, reference, libelle.strip()[:160],
             json.dumps(donnees or {}, ensure_ascii=False), _horodatage()),
        )
        base.commit()
        ligne = base.execute(
            "SELECT * FROM tendance_sauvegardes WHERE id = ?",
            (curseur.lastrowid,)).fetchone()
    return _sauvegarde_depuis_ligne(ligne)


def sauvegardes(utilisateur_id: int, *, type_: str | None = None) -> list[dict]:
    base = _connexion()
    if type_:
        lignes = base.execute(
            "SELECT * FROM tendance_sauvegardes WHERE utilisateur_id = ? "
            "AND type = ? ORDER BY cree_le DESC", (utilisateur_id, type_)
        ).fetchall()
    else:
        lignes = base.execute(
            "SELECT * FROM tendance_sauvegardes WHERE utilisateur_id = ? "
            "ORDER BY cree_le DESC", (utilisateur_id,)).fetchall()
    return [_sauvegarde_depuis_ligne(l) for l in lignes]


def _sauvegarde_depuis_ligne(ligne: sqlite3.Row) -> dict:
    try:
        donnees = json.loads(ligne["donnees"])
    except (json.JSONDecodeError, TypeError):
        donnees = {}
    return {
        "id": ligne["id"], "type": ligne["type"], "reference": ligne["reference"],
        "libelle": ligne["libelle"], "donnees": donnees, "cree_le": ligne["cree_le"],
    }


def supprimer_sauvegarde(utilisateur_id: int, sauvegarde_id: int) -> bool:
    """Vrai si une ligne appartenant à ce compte a bien été supprimée — la
    condition `utilisateur_id` dans la clause WHERE est le cloisonnement,
    au même titre que `store.get(id, owner=...)` pour les projets."""
    with _LOCK:
        base = _connexion()
        curseur = base.execute(
            "DELETE FROM tendance_sauvegardes WHERE id = ? AND utilisateur_id = ?",
            (sauvegarde_id, utilisateur_id))
        base.commit()
    return curseur.rowcount > 0


# --- Veille (#8) : architecture, aucun ordonnanceur branché -----------------
# `verifier_surveillances()` est le point d'entrée qu'un futur planificateur
# (cron, tâche systemd, APScheduler…) appellera périodiquement. Rien
# n'appelle encore cette fonction aujourd'hui : une surveillance créée reste
# un vœu enregistré, `derniere_execution` reste vide, et l'interface le dit
# plutôt que de laisser croire à une veille active.
def creer_surveillance(utilisateur_id: int, libelle: str, requete: dict,
                       *, frequence_heures: int = 24) -> dict:
    with _LOCK:
        base = _connexion()
        curseur = base.execute(
            "INSERT INTO tendance_surveillances (utilisateur_id, libelle, "
            "requete, frequence_heures, cree_le) VALUES (?,?,?,?,?)",
            (utilisateur_id, libelle.strip()[:160],
             json.dumps(requete, ensure_ascii=False),
             max(1, frequence_heures), _horodatage()),
        )
        base.commit()
        ligne = base.execute(
            "SELECT * FROM tendance_surveillances WHERE id = ?",
            (curseur.lastrowid,)).fetchone()
    return _surveillance_depuis_ligne(ligne)


def surveillances(utilisateur_id: int) -> list[dict]:
    base = _connexion()
    lignes = base.execute(
        "SELECT * FROM tendance_surveillances WHERE utilisateur_id = ? "
        "ORDER BY cree_le DESC", (utilisateur_id,)).fetchall()
    return [_surveillance_depuis_ligne(l) for l in lignes]


def _surveillance_depuis_ligne(ligne: sqlite3.Row) -> dict:
    try:
        requete = json.loads(ligne["requete"])
    except (json.JSONDecodeError, TypeError):
        requete = {}
    return {
        "id": ligne["id"], "libelle": ligne["libelle"], "requete": requete,
        "frequence_heures": ligne["frequence_heures"], "actif": bool(ligne["actif"]),
        "derniere_execution": ligne["derniere_execution"], "cree_le": ligne["cree_le"],
    }


def supprimer_surveillance(utilisateur_id: int, surveillance_id: int) -> bool:
    with _LOCK:
        base = _connexion()
        curseur = base.execute(
            "DELETE FROM tendance_surveillances WHERE id = ? AND utilisateur_id = ?",
            (surveillance_id, utilisateur_id))
        base.commit()
    return curseur.rowcount > 0


def verifier_surveillances(*, maintenant: float | None = None) -> int:
    """Relève chaque surveillance active dont la fréquence est dépassée, en
    réutilisant `fetch_all` sur les liens de sa requête.

    Personne n'appelle encore cette fonction aujourd'hui — c'est exactement
    ce que demande la consigne « prévoir l'architecture » sans l'activer.
    Un futur cron ou tâche planifiée n'a qu'à l'appeler périodiquement.
    Retourne le nombre de surveillances relevées.
    """
    maintenant = maintenant if maintenant is not None else time.time()
    base = _connexion()
    lignes = base.execute(
        "SELECT * FROM tendance_surveillances WHERE actif = 1").fetchall()
    traitees = 0
    for ligne in lignes:
        derniere = ligne["derniere_execution"]
        if derniere:
            epoque = _depuis_iso(derniere)
            ecoulees = ((maintenant - epoque) / 3600 if epoque is not None
                       else ligne["frequence_heures"])
            if ecoulees < ligne["frequence_heures"]:
                continue
        try:
            requete = json.loads(ligne["requete"])
        except (json.JSONDecodeError, TypeError):
            requete = {}
        for video in fetch_all(requete.get("liens") or []):
            enregistrer_releve(ligne["utilisateur_id"], video)
        with _LOCK:
            base.execute(
                "UPDATE tendance_surveillances SET derniere_execution = ? "
                "WHERE id = ?",
                (_horodatage(maintenant), ligne["id"]),
            )
            base.commit()
        traitees += 1
    return traitees


# --- Analyse d'une vidéo (#4) -----------------------------------------------
_MOTS_CTA = (
    "abonne", "abonn", "commente", "commentaire", "partage", "like",
    "suis-moi", "clique", "lien en bio", "sauvegarde", "enregistre",
)


def _detecter_cta(texte: str) -> str:
    """Cherche une formule d'appel à l'action connue dans le texte.

    Un simple repérage de mots-clés, pas une compréhension du sens : un
    texte qui n'en contient aucun renvoie une chaîne vide plutôt qu'une
    supposition sur ce que la vidéo demande.
    """
    minuscule = texte.lower()
    trouve = [mot for mot in _MOTS_CTA if mot in minuscule]
    if not trouve:
        return ""
    for phrase in re.split(r"(?<=[.!?…])\s+", texte):
        if any(mot in phrase.lower() for mot in trouve):
            return phrase.strip()
    return ""


def _decouper_en_tiers(texte: str) -> dict[str, str]:
    """Découpage approximatif par tiers du nombre de mots — pas une analyse
    sémantique de la structure, seulement un repère de lecture."""
    mots = texte.split()
    if not mots:
        return {"ouverture": "", "developpement": "", "chute": ""}
    a = max(1, len(mots) // 3)
    b = max(a + 1, 2 * len(mots) // 3)
    return {
        "ouverture": " ".join(mots[:a]),
        "developpement": " ".join(mots[a:b]),
        "chute": " ".join(mots[b:]),
    }


@dataclass
class AnalyseVideo:
    video: VideoTendance
    sujet: str
    hook_extrait: str
    structure: dict[str, str]
    cta_detecte: str
    duree_secondes: float | None
    nombre_de_plans: int | None
    rythme_coupes_par_minute: float | None
    transcription: str
    langue: str
    limites: list[str]

    def to_dict(self) -> dict:
        base = self.video.to_dict()
        base["analyse"].update({
            "sujet": self.sujet,
            "hook_extrait": self.hook_extrait,
            "structure": self.structure,
            "cta_detecte": self.cta_detecte,
            "duree_secondes": self.duree_secondes,
            "nombre_de_plans": self.nombre_de_plans,
            "rythme_coupes_par_minute": self.rythme_coupes_par_minute,
            "transcription": self.transcription,
            "langue": self.langue,
            "limites": self.limites,
        })
        return base


def analyser_video(url: str, *, dossier: Path, avec_transcription: bool) -> AnalyseVideo:
    """Analyse une vidéo à partir de son lien : métadonnées réelles, plans et
    rythme mesurés sur le fichier, transcription si demandée et disponible.

    `dossier` est l'espace du compte qui demande l'analyse — même
    cloisonnement que le reste de l'application — et les fichiers
    intermédiaires (vidéo, audio) y sont effacés à la sortie : le Trend
    Finder n'a pas besoin de garder le fichier une fois l'analyse faite.
    Chaque limite (donnée non calculable, transcription indisponible…) est
    listée en clair plutôt que masquée.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    meta = fetch_metadata(url)

    limites: list[str] = []
    plans: list[float] | None = None
    duree_reelle: float | None = meta.duree
    transcription = ""
    langue = ""

    telechargement = downloader.download_one(url, dossier, 1)
    try:
        if not telechargement.ok:
            limites.append(
                f"Vidéo non téléchargeable pour l'analyse fine : "
                f"{telechargement.error}")
        else:
            chemin = Path(telechargement.path)
            duree_reelle = telechargement.duration or meta.duree
            try:
                plans = analyzer.detect_scenes(chemin)
            except MediaError as exc:
                limites.append(f"Détection des plans impossible : {exc}")

            if avec_transcription:
                if not transcribe.available():
                    limites.append(
                        "Transcription indisponible : "
                        f"{transcribe.raison_indisponible()}.")
                else:
                    try:
                        audio = transcribe.ensure_audio(chemin, dossier / "audio.wav")
                        resultat = transcribe.transcribe(audio)
                        transcription, langue = resultat.text, resultat.language
                    except (MediaError, transcribe.TranscriptionError) as exc:
                        limites.append(f"Transcription impossible : {exc}")
            else:
                limites.append(
                    "Transcription non demandée : réservée aux formules "
                    "Créateur et Studio.")
    finally:
        for reste in dossier.glob("source_01.*"):
            reste.unlink(missing_ok=True)
        (dossier / "audio.wav").unlink(missing_ok=True)

    rythme = None
    if plans is not None and duree_reelle:
        rythme = round((len(plans) + 1) / (duree_reelle / 60), 1)

    texte_pour_analyse = transcription or meta.titre
    if not transcription:
        limites.append(
            "Sujet, structure, hook et appel à l'action estimés à partir du "
            "titre/de la description faute de transcription : moins fiables "
            "qu'un texte intégral.")

    return AnalyseVideo(
        video=meta,
        sujet=meta.titre,
        hook_extrait=" ".join(texte_pour_analyse.split()[:18]) if texte_pour_analyse else "",
        structure=_decouper_en_tiers(texte_pour_analyse),
        cta_detecte=_detecter_cta(texte_pour_analyse) if texte_pour_analyse else "",
        duree_secondes=duree_reelle,
        nombre_de_plans=(len(plans) + 1) if plans is not None else None,
        rythme_coupes_par_minute=rythme,
        transcription=transcription,
        langue=langue,
        limites=limites,
    )
