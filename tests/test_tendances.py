"""Trend Finder : analyses jamais inventées, cloisonnement, veille inerte."""

from __future__ import annotations

from pathlib import Path

import pytest

from flambee import account, downloader, media, scriptgen, trends, users


# --- VideoTendance : rien n'est inventé -------------------------------
def test_engagement_none_si_un_compteur_manque():
    v = trends.VideoTendance(url="u", vues=1000, likes=100, commentaires=None, partages=5)
    assert v.engagement is None


def test_engagement_calcule_quand_tout_est_connu():
    v = trends.VideoTendance(url="u", vues=1000, likes=80, commentaires=15, partages=5)
    assert v.engagement == pytest.approx(0.1)


def test_engagement_none_sans_vues():
    v = trends.VideoTendance(url="u", vues=None, likes=1, commentaires=1, partages=1)
    assert v.engagement is None
    assert v.vitesse_vues_heure is None
    assert v.score_pepite is None


def test_vitesse_vues_heure_none_sans_date_de_publication():
    v = trends.VideoTendance(url="u", vues=10_000)
    assert v.anciennete_heures is None
    assert v.vitesse_vues_heure is None


def test_score_pepite_degrade_sans_engagement_plutot_que_de_l_annuler():
    import time
    publie = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 10 * 3600))
    v = trends.VideoTendance(url="u", vues=10_000, publie_le=publie)
    assert v.engagement is None
    assert v.vitesse_vues_heure == pytest.approx(1000, rel=0.05)
    assert v.score_pepite == pytest.approx(1000, rel=0.05)  # multiplicateur = 1


def test_to_dict_separe_donnees_et_analyse():
    v = trends.VideoTendance(url="u", vues=100, likes=10, commentaires=1, partages=1)
    d = v.to_dict()
    assert set(d) == {"donnees", "ok", "erreur", "analyse"}
    assert "vues" in d["donnees"]
    assert "engagement" in d["analyse"]
    assert "vues" not in d["analyse"]


# --- Extraction de métadonnées (sans réseau) ---------------------------
def test_depuis_info_extrait_hashtags_et_date():
    info = {
        "webpage_url": "https://www.tiktok.com/@x/video/1",
        "description": "Regarde ça #cuisine #RapideEtBon",
        "uploader": "x", "view_count": 5000, "like_count": 400,
        "comment_count": 12, "repost_count": 8, "duration": 27.4,
        "timestamp": 1_700_000_000, "extractor_key": "TikTok",
        "thumbnail": "https://x/th.jpg",
    }
    v = trends._depuis_info(info["webpage_url"], info)
    assert v.hashtags == ["cuisine", "rapideetbon"]
    assert v.vues == 5000 and v.partages == 8
    assert v.publie_le.startswith("2023-")
    assert v.id  # empreinte stable, non vide


def test_depuis_info_garde_un_vrai_zero_partages():
    """`0 partage or share_count` perdrait ce zéro : il doit être conservé tel quel."""
    info = {"webpage_url": "https://www.tiktok.com/@x/video/2",
           "repost_count": 0, "share_count": 999}
    v = trends._depuis_info(info["webpage_url"], info)
    assert v.partages == 0


def test_fetch_metadata_renvoie_une_erreur_lisible(monkeypatch):
    import yt_dlp
    from yt_dlp.utils import DownloadError as YdlError

    class FausseYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            raise YdlError("ERROR: [TikTok] Video unavailable")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FausseYDL)
    v = trends.fetch_metadata("https://tiktok.com/@x/video/1")
    assert not v.ok
    assert "indisponible" in v.erreur.lower()


# --- Filtres, jamais une requête à TikTok -------------------------------
def _video(**kwargs) -> trends.VideoTendance:
    import hashlib

    base = dict(url="u", titre="Une recette rapide", createur="chef",
               vues=1000, hashtags=["cuisine"], publie_le="2026-06-01T00:00:00")
    base.update(kwargs)
    if not base.get("id"):
        base["id"] = hashlib.sha256(base["url"].encode("utf-8")).hexdigest()[:16]
    return trends.VideoTendance(**base)


def test_filtre_mots_cles():
    videos = [_video(titre="Recette de pâtes"), _video(titre="Voyage au Japon")]
    resultat = trends.appliquer_filtres(videos, mots_cles="pâtes")
    assert len(resultat) == 1 and "pâtes" in resultat[0].titre.lower()


def test_filtre_hashtags():
    videos = [_video(hashtags=["cuisine"]), _video(hashtags=["voyage"])]
    resultat = trends.appliquer_filtres(videos, hashtags=["voyage"])
    assert len(resultat) == 1 and resultat[0].hashtags == ["voyage"]


def test_filtre_vues_min():
    videos = [_video(vues=100), _video(vues=100_000)]
    resultat = trends.appliquer_filtres(videos, vues_min=1000)
    assert len(resultat) == 1 and resultat[0].vues == 100_000


def test_filtre_periode():
    videos = [_video(publie_le="2026-01-01T00:00:00"),
             _video(publie_le="2026-08-01T00:00:00")]
    resultat = trends.appliquer_filtres(videos, depuis_le="2026-06-01T00:00:00")
    assert len(resultat) == 1 and resultat[0].publie_le == "2026-08-01T00:00:00"


def test_filtre_garde_toujours_les_echecs():
    videos = [_video(vues=1_000_000), trends.VideoTendance(url="cassee", erreur="échec")]
    resultat = trends.appliquer_filtres(videos, vues_min=5_000_000)
    assert len(resultat) == 1 and not resultat[0].ok


def test_periode_vers_date():
    assert trends.periode_vers_date("") == ""
    assert trends.periode_vers_date("2026") == "2026-01-01T00:00:00"
    assert trends.periode_vers_date("7j") != ""


# --- Heuristiques d'analyse (pas d'invention) ---------------------------
def test_detecter_cta_trouve_une_formule_connue():
    texte = "Voici l'astuce. Abonne-toi pour la suite !"
    assert "abonne" in trends._detecter_cta(texte).lower()


def test_detecter_cta_vide_si_rien_ne_correspond():
    assert trends._detecter_cta("Un texte neutre sans rien de spécial.") == ""


def test_decouper_en_tiers_texte_vide():
    tiers = trends._decouper_en_tiers("")
    assert tiers == {"ouverture": "", "developpement": "", "chute": ""}


def test_decouper_en_tiers_repartit_les_mots():
    texte = " ".join(f"mot{i}" for i in range(9))
    tiers = trends._decouper_en_tiers(texte)
    assert len(tiers["ouverture"].split()) == 3
    assert len(tiers["developpement"].split()) == 3
    assert len(tiers["chute"].split()) == 3


# --- Base de données : cloisonnement, pépites, hashtags, veille --------
def _premier_compte(espace) -> int:
    return users.creer("essai@exemple.fr", "motdepasse1").id


def test_enregistrer_releve_insere_video_et_releve(espace):
    uid = _premier_compte(espace)
    v = _video(url="https://tiktok.com/@x/video/1", id="", vues=100)
    trends.enregistrer_releve(uid, v)
    base = trends._connexion()
    videos = base.execute("SELECT * FROM tendance_videos").fetchall()
    releves = base.execute("SELECT * FROM tendance_releves").fetchall()
    assert len(videos) == 1 and len(releves) == 1
    assert releves[0]["utilisateur_id"] == uid


def test_enregistrer_releve_ignore_les_echecs(espace):
    uid = _premier_compte(espace)
    trends.enregistrer_releve(uid, trends.VideoTendance(url="u", erreur="échec"))
    base = trends._connexion()
    assert base.execute("SELECT COUNT(*) AS n FROM tendance_releves").fetchone()["n"] == 0


def test_pepites_distingue_mesure_et_estimation(espace):
    uid = _premier_compte(espace)
    video_id = "abc123"
    base = trends._connexion()
    base.execute(
        "INSERT INTO tendance_videos (id, url, plateforme, titre, createur, "
        "miniature, duree, publie_le, hashtags, premiere_fois) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (video_id, "https://tiktok.com/@x/video/1", "TikTok", "Test", "x",
         "", 30.0, "2026-09-10T00:00:00", "[]", "2026-09-10T00:00:00"),
    )
    # Deux relevés, deux heures d'écart, une vraie progression mesurable.
    base.execute(
        "INSERT INTO tendance_releves (video_id, utilisateur_id, vues, likes, "
        "commentaires, partages, releve_le) VALUES (?,?,?,?,?,?,?)",
        (video_id, uid, 1000, 50, 5, 2, "2026-09-17T10:00:00"))
    base.execute(
        "INSERT INTO tendance_releves (video_id, utilisateur_id, vues, likes, "
        "commentaires, partages, releve_le) VALUES (?,?,?,?,?,?,?)",
        (video_id, uid, 5000, 200, 20, 10, "2026-09-17T12:00:00"))
    base.commit()

    resultat = trends.pepites(uid)
    assert len(resultat) == 1
    analyse = resultat[0]["analyse"]
    assert analyse["mesuree"] is True
    assert analyse["croissance_mesuree_vues_heure"] == pytest.approx(2000, rel=0.01)
    assert analyse["nombre_de_releves"] == 2


def test_pepites_cloisonnees_par_compte(espace):
    uid_a = _premier_compte(espace)
    uid_b = users.creer("b@exemple.fr", "motdepasse1").id
    trends.enregistrer_releve(uid_a, _video(url="https://tiktok.com/@a/video/1",
                                            publie_le="2026-09-01T00:00:00"))
    assert trends.pepites(uid_b) == []
    assert len(trends.pepites(uid_a)) == 1


def test_hashtags_observes_compte_les_videos(espace):
    uid = _premier_compte(espace)
    trends.enregistrer_releve(uid, _video(url="https://tiktok.com/@a/video/1",
                                          hashtags=["cuisine", "rapide"]))
    trends.enregistrer_releve(uid, _video(url="https://tiktok.com/@a/video/2",
                                          hashtags=["cuisine"]))
    hashtags = {h["hashtag"]: h for h in trends.hashtags_observes(uid)}
    assert hashtags["cuisine"]["analyse"]["nombre_de_videos_observees"] == 2
    assert hashtags["rapide"]["analyse"]["nombre_de_videos_observees"] == 1


def test_sauvegardes_crud_et_cloisonnement(espace):
    uid_a = _premier_compte(espace)
    uid_b = users.creer("b@exemple.fr", "motdepasse1").id

    sauvegarde = trends.sauvegarder(uid_a, "video", "https://tiktok.com/@a/video/1",
                                    libelle="Ma trouvaille")
    assert trends.sauvegardes(uid_a)[0]["id"] == sauvegarde["id"]
    assert trends.sauvegardes(uid_b) == []

    # Le compte B ne peut pas effacer la sauvegarde du compte A.
    assert trends.supprimer_sauvegarde(uid_b, sauvegarde["id"]) is False
    assert trends.supprimer_sauvegarde(uid_a, sauvegarde["id"]) is True
    assert trends.sauvegardes(uid_a) == []


def test_sauvegarder_type_inconnu_refuse(espace):
    uid = _premier_compte(espace)
    with pytest.raises(ValueError):
        trends.sauvegarder(uid, "n_importe_quoi", "ref")


def test_veille_creation_et_inertie(espace):
    """Une surveillance créée reste un vœu enregistré : rien ne l'exécute."""
    uid = _premier_compte(espace)
    surveillance = trends.creer_surveillance(
        uid, "Ma veille", {"liens": ["https://tiktok.com/@a/video/1"]},
        frequence_heures=24,
    )
    assert surveillance["derniere_execution"] == ""
    assert surveillance["actif"] is True
    assert trends.surveillances(uid)[0]["id"] == surveillance["id"]


def test_verifier_surveillances_appelle_fetch_all_et_horodate(espace, monkeypatch):
    uid = _premier_compte(espace)
    trends.creer_surveillance(uid, "Veille", {"liens": ["https://tiktok.com/@a/video/1"]},
                              frequence_heures=1)

    appels = []

    def faux_fetch_all(urls, **kwargs):
        appels.append(urls)
        return [_video(url=urls[0])]

    monkeypatch.setattr(trends, "fetch_all", faux_fetch_all)
    traitees = trends.verifier_surveillances()
    assert traitees == 1
    assert appels == [["https://tiktok.com/@a/video/1"]]
    assert trends.surveillances(uid)[0]["derniere_execution"] != ""


def test_verifier_surveillances_respecte_la_frequence(espace, monkeypatch):
    uid = _premier_compte(espace)
    surveillance = trends.creer_surveillance(
        uid, "Veille", {"liens": ["https://tiktok.com/@a/video/1"]}, frequence_heures=999)
    base = trends._connexion()
    base.execute("UPDATE tendance_surveillances SET derniere_execution = ? WHERE id = ?",
                 ("2026-09-18T00:00:00", surveillance["id"]))
    base.commit()

    monkeypatch.setattr(trends, "fetch_all", lambda urls, **k: pytest.fail("pas dû tourner"))
    import time
    traitees = trends.verifier_surveillances(
        maintenant=time.mktime(time.strptime("2026-09-18T01:00:00", "%Y-%m-%dT%H:%M:%S")))
    assert traitees == 0


def test_supprimer_surveillance_cloisonnee(espace):
    uid_a = _premier_compte(espace)
    uid_b = users.creer("b@exemple.fr", "motdepasse1").id
    s = trends.creer_surveillance(uid_a, "V", {"liens": []})
    assert trends.supprimer_surveillance(uid_b, s["id"]) is False
    assert trends.supprimer_surveillance(uid_a, s["id"]) is True


# --- Analyse d'une vidéo : fichier réel, jamais de fausse donnée --------
def _tiny_video(path: Path, seconds: int = 3) -> None:
    media.ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=size=320x568:rate=15:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
    ])


def test_analyser_video_sans_transcription(espace, monkeypatch, tmp_path):
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    clip = tmp_path / "source.mp4"
    _tiny_video(clip)

    def faux_download_one(url, dest_dir, index, *, cancel=None):
        cible = dest_dir / f"source_{index:02d}.mp4"
        cible.write_bytes(clip.read_bytes())
        return downloader.Source(index=index, url=url, path=str(cible),
                                 title="Vidéo test", duration=3.0)

    monkeypatch.setattr(trends.downloader, "download_one", faux_download_one)
    monkeypatch.setattr(trends, "fetch_metadata",
                        lambda url: _video(url=url, titre="Sujet observé",
                                           hashtags=["cuisine"]))

    dossier = tmp_path / "analyse"
    analyse = trends.analyser_video("https://tiktok.com/@x/video/1", dossier=dossier,
                                    avec_transcription=False)

    assert analyse.duree_secondes == pytest.approx(3.0)
    assert analyse.nombre_de_plans is not None and analyse.nombre_de_plans >= 1
    assert analyse.transcription == ""
    assert any("non demandée" in l for l in analyse.limites)
    assert not (dossier / "source_01.mp4").exists()   # nettoyé après usage


def test_analyser_video_lien_impossible_a_telecharger(espace, monkeypatch, tmp_path):
    def faux_download_one(url, dest_dir, index, *, cancel=None):
        return downloader.Source(index=index, url=url, error="Vidéo privée.")

    monkeypatch.setattr(trends.downloader, "download_one", faux_download_one)
    monkeypatch.setattr(trends, "fetch_metadata",
                        lambda url: _video(url=url, titre="X"))

    analyse = trends.analyser_video("https://tiktok.com/@x/video/1",
                                    dossier=tmp_path / "a", avec_transcription=False)
    assert analyse.nombre_de_plans is None
    assert any("non téléchargeable" in l for l in analyse.limites)


# --- Inspiration (scriptgen) : jamais une copie -------------------------
def test_inspirer_sans_cle_api(monkeypatch):
    monkeypatch.setattr(scriptgen, "api_key_available", lambda: False)
    with pytest.raises(scriptgen.ScriptError):
        scriptgen.inspirer_depuis_tendance({"sujet": "cuisine"})


def test_inspirer_appelle_claude_avec_les_caracteristiques_pas_le_texte(monkeypatch):
    import anthropic
    from types import SimpleNamespace

    monkeypatch.setattr(scriptgen, "api_key_available", lambda: True)
    capture = {}

    class FausseReponse:
        content = [SimpleNamespace(type="text", text="Une idée neuve.")]

    class FauxMessages:
        def create(self, **kwargs):
            capture.update(kwargs)
            return FausseReponse()

    class FauxClient:
        def __init__(self, *a, **k):
            self.messages = FauxMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FauxClient)
    idee = scriptgen.inspirer_depuis_tendance({
        "sujet": "Recette de pâtes", "hook_extrait": "Ton riz colle ?",
        "cta_detecte": "Abonne-toi", "hashtags": ["cuisine"],
        "transcription": "TEXTE INTÉGRAL QUI NE DOIT PAS PARTIR",
    })
    assert idee == "Une idée neuve."
    assert "TEXTE INTÉGRAL" not in capture["messages"][0]["content"]
    assert "Recette de pâtes" in capture["messages"][0]["content"]


# --- Routes : authentification, cloisonnement, formats de réponse ------
def test_page_tendances_exige_un_compte(client):
    r = client.get("/studio/tendances", follow_redirects=False)
    assert r.status_code == 303
    assert "/connexion" in r.headers["location"]


def test_page_tendances_accessible_en_essai(compte):
    r = compte.get("/studio/tendances")
    assert r.status_code == 200
    assert "Trend" in r.text


def test_api_recherche_exige_un_compte(client):
    r = client.post("/api/tendances/recherche", json={"liens": "https://tiktok.com/@x/video/1"})
    assert r.status_code == 401


def test_api_recherche_liste_les_echecs_sans_inventer(compte, monkeypatch):
    monkeypatch.setattr(trends, "fetch_metadata",
                        lambda url: trends.VideoTendance(url=url, erreur="Vidéo privée."))
    r = compte.post("/api/tendances/recherche",
                    json={"liens": "https://tiktok.com/@x/video/1"})
    assert r.status_code == 200
    corps = r.json()
    assert corps["total_recu"] == 1
    assert corps["echecs"][0]["erreur"] == "Vidéo privée."


def test_api_recherche_persiste_un_releve_pour_le_compte(compte, monkeypatch):
    monkeypatch.setattr(trends, "fetch_metadata",
                        lambda url: _video(url=url, vues=42))
    compte.post("/api/tendances/recherche", json={"liens": "https://tiktok.com/@x/video/1"})
    r = compte.get("/api/tendances/pepites")
    assert len(r.json()["pepites"]) == 1


def test_api_recherche_refuse_sans_lien(compte):
    r = compte.post("/api/tendances/recherche", json={"liens": ""})
    assert r.status_code == 400


def test_api_sauvegardes_cloisonnees_entre_comptes(compte):
    from fastapi.testclient import TestClient

    from flambee import app as app_module

    compte.post("/api/tendances/sauvegardes",
               json={"type": "hashtag", "reference": "cuisine", "libelle": "cuisine"})
    sauvegarde_id = compte.get("/api/tendances/sauvegardes").json()["sauvegardes"][0]["id"]

    # Un deuxième client, avec son propre pot de cookies : `compte` réutilisé
    # se serait reconnecté sous la nouvelle identité, faussant le test.
    with TestClient(app_module.app) as autre:
        autre.post("/inscription", data={"email": "autre@exemple.fr",
                                         "mot_de_passe": "motdepasse1", "nom": "Autre"})
        r = autre.delete(f"/api/tendances/sauvegardes/{sauvegarde_id}")
        assert r.status_code == 404

    assert len(compte.get("/api/tendances/sauvegardes").json()["sauvegardes"]) == 1


def test_api_analyser_reserve_la_transcription_a_la_formule_pro(compte, monkeypatch, tmp_path):
    """En Essai, l'analyse tourne mais sans transcription — jamais de faux texte."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    clip = tmp_path / "source.mp4"
    _tiny_video(clip)

    def faux_download_one(url, dest_dir, index, *, cancel=None):
        cible = dest_dir / f"source_{index:02d}.mp4"
        cible.write_bytes(clip.read_bytes())
        return downloader.Source(index=index, url=url, path=str(cible), duration=3.0)

    monkeypatch.setattr(trends.downloader, "download_one", faux_download_one)
    monkeypatch.setattr(trends, "fetch_metadata", lambda url: _video(url=url))
    r = compte.post("/api/tendances/analyser", json={"url": "https://tiktok.com/@x/video/1"})
    assert r.status_code == 200
    assert r.json()["analyse"]["transcription"] == ""
    assert any("Créateur et Studio" in l for l in r.json()["analyse"]["limites"])


def test_api_veille_creation_liste_suppression(compte):
    r = compte.post("/api/tendances/veille",
                    json={"libelle": "Ma veille", "liens": "https://tiktok.com/@x/video/1"})
    assert r.status_code == 200
    veille_id = r.json()["id"]
    assert len(compte.get("/api/tendances/veille").json()["surveillances"]) == 1
    assert compte.delete(f"/api/tendances/veille/{veille_id}").status_code == 200
    assert compte.get("/api/tendances/veille").json()["surveillances"] == []


def test_tendances_transcription_autorisee_suit_la_formule(compte_pro):
    createur = users.par_email("essai@exemple.fr")   # compte_pro : passé en Créateur
    assert account.tendances_transcription_autorisee(createur) is True

    essai = users.creer("autre-essai@exemple.fr", "motdepasse1")
    assert account.tendances_transcription_autorisee(essai) is False
