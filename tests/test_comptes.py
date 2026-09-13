"""Comptes, sessions et cloisonnement entre utilisateurs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import account, app as app_module, config, media, users  # noqa: E402
from flambee import voicestudio  # noqa: E402
from flambee.project import store  # noqa: E402


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "ensure_tools", lambda: [])
    monkeypatch.setattr(app_module.media, "ensure_tools", lambda: [])
    for module in (config, users.config, voicestudio.config):
        monkeypatch.setattr(module, "WORK_DIR", tmp_path, raising=False)
    monkeypatch.setattr(users._local, "db", None, raising=False)
    monkeypatch.setattr(config, "PASSWORD", "")
    store._cache.clear()
    with TestClient(app_module.app) as test_client:
        yield test_client


def inscrire(client, email="a@exemple.fr", mot_de_passe="motdepasse1", **extra):
    return client.post("/inscription",
                       data={"email": email, "mot_de_passe": mot_de_passe,
                             "nom": extra.pop("nom", "Test"), **extra},
                       follow_redirects=False)


# --- Inscription et connexion ---------------------------------------------
def test_inscription_ouvre_la_session(client):
    reponse = inscrire(client)
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/studio"
    assert users.SESSION_COOKIE in reponse.cookies
    # L'atelier est accessible dans la foulée.
    assert client.get("/studio").status_code == 200


def test_le_cookie_est_protege(client):
    reponse = inscrire(client)
    entete = reponse.headers["set-cookie"].lower()
    assert "httponly" in entete          # inaccessible au JavaScript
    assert "samesite=lax" in entete      # pas envoyé depuis un autre site


def test_inscription_refuse_les_doublons_et_les_mots_de_passe_faibles(client):
    inscrire(client)
    client.cookies.clear()
    assert inscrire(client).status_code == 400
    assert inscrire(client, email="b@exemple.fr",
                    mot_de_passe="court").status_code == 400


def test_connexion_et_deconnexion(client):
    inscrire(client)
    client.cookies.clear()

    mauvais = client.post("/connexion", data={"email": "a@exemple.fr",
                                              "mot_de_passe": "faux"})
    assert mauvais.status_code == 401

    bon = client.post("/connexion", data={"email": "a@exemple.fr",
                                          "mot_de_passe": "motdepasse1"},
                      follow_redirects=False)
    assert bon.status_code == 303
    assert client.get("/studio").status_code == 200

    client.get("/deconnexion", follow_redirects=False)
    assert client.get("/studio", follow_redirects=False).status_code == 303


def test_atelier_ferme_aux_visiteurs(client):
    reponse = client.get("/studio", follow_redirects=False)
    assert reponse.status_code == 303
    assert "/connexion?suite=/studio" in reponse.headers["location"]
    # L'API répond 401 plutôt que de rediriger.
    assert client.post("/api/projects").status_code == 401


def test_le_site_public_reste_ouvert(client):
    for chemin in ("/", "/tarifs", "/faq", "/connexion", "/inscription",
                   "/mentions-legales"):
        assert client.get(chemin).status_code == 200, chemin


def test_redirection_apres_connexion_reste_interne(client):
    """Une URL absolue permettrait d'expédier l'utilisateur ailleurs."""
    inscrire(client)
    client.cookies.clear()
    reponse = client.post("/connexion",
                          data={"email": "a@exemple.fr", "mot_de_passe": "motdepasse1",
                                "suite": "https://ailleurs.test/piege"},
                          follow_redirects=False)
    assert reponse.headers["location"] == "/studio"


# --- Cloisonnement ---------------------------------------------------------
def test_un_compte_ne_voit_pas_les_projets_dun_autre(client):
    inscrire(client, email="un@exemple.fr")
    projet = client.post("/api/projects").json()
    assert client.get(f"/api/projects/{projet['id']}").status_code == 200

    client.cookies.clear()
    inscrire(client, email="deux@exemple.fr")
    # Même identifiant, autre compte : introuvable.
    assert client.get(f"/api/projects/{projet['id']}").status_code == 404
    assert client.get("/api/projects").json()["projects"] == []


def test_les_crédits_sont_propres_a_chaque_compte(client):
    inscrire(client, email="un@exemple.fr")
    un = users.par_email("un@exemple.fr")
    account.noter(un, "rendu", "x")
    assert account.restants(users.par_email("un@exemple.fr")) == 2

    client.cookies.clear()
    inscrire(client, email="deux@exemple.fr")
    assert account.restants(users.par_email("deux@exemple.fr")) == 3


def test_la_voix_importee_est_propre_au_compte(client, tmp_path):
    un, deux = 1, 2
    voicestudio.dossier(un).mkdir(parents=True, exist_ok=True)
    (voicestudio.chemin_audio(un)).write_bytes(b"faux")
    (voicestudio.chemin_infos(un)).write_text('{"nom":"a","duree":1,"mots":[]}')
    assert voicestudio.charger(un) is not None
    assert voicestudio.charger(deux) is None


# --- Code d'invitation -----------------------------------------------------
def test_code_dinvitation(client, monkeypatch):
    monkeypatch.setenv("FLAMBEE_INVITE_CODE", "sesame")
    assert inscrire(client, email="c@exemple.fr").status_code == 400
    assert inscrire(client, email="c@exemple.fr",
                    invitation="sesame").status_code == 303


def test_inscriptions_fermees(client, monkeypatch):
    inscrire(client, email="premier@exemple.fr")
    client.cookies.clear()
    monkeypatch.setenv("FLAMBEE_SIGNUP", "ferme")
    assert inscrire(client, email="second@exemple.fr").status_code == 400


# --- Mot de passe et suppression -------------------------------------------
def test_changer_de_mot_de_passe(client):
    inscrire(client)
    assert client.post("/studio/profil/mot-de-passe",
                       data={"ancien": "faux", "nouveau": "nouveaumdp1"}).status_code == 400
    assert client.post("/studio/profil/mot-de-passe",
                       data={"ancien": "motdepasse1", "nouveau": "nouveaumdp1"},
                       follow_redirects=False).status_code == 303
    client.cookies.clear()
    assert client.post("/connexion", data={"email": "a@exemple.fr",
                                           "mot_de_passe": "nouveaumdp1"},
                       follow_redirects=False).status_code == 303


def test_suppression_de_compte(client):
    inscrire(client)
    identifiant = users.par_email("a@exemple.fr").id
    espace = users.par_id(identifiant).dossier
    espace.mkdir(parents=True, exist_ok=True)
    (espace / "trace.txt").write_text("x")

    assert client.post("/studio/profil/supprimer",
                       data={"confirmation": "non"},
                       follow_redirects=False).status_code == 303
    assert users.par_email("a@exemple.fr") is not None      # rien sans confirmation

    client.post("/studio/profil/supprimer", data={"confirmation": "SUPPRIMER"},
                follow_redirects=False)
    assert users.par_email("a@exemple.fr") is None
    assert not espace.exists()


# --- Mots de passe ---------------------------------------------------------
def test_les_mots_de_passe_ne_sont_jamais_stockes_en_clair(client, tmp_path):
    inscrire(client, mot_de_passe="secretsecret")
    # En mode WAL, les écritures récentes vivent dans le fichier -wal :
    # il faut inspecter les deux pour conclure.
    contenu = b"".join(fichier.read_bytes()
                       for fichier in tmp_path.glob("flambee.db*"))
    assert b"secretsecret" not in contenu
    assert b"scrypt$" in contenu


def test_deux_comptes_meme_mot_de_passe_ont_des_empreintes_differentes():
    a, b = users.hacher("identique"), users.hacher("identique")
    assert a != b                       # le sel diffère
    assert users.verifier("identique", a) and users.verifier("identique", b)
    assert not users.verifier("autre", a)


# --- Mot de passe oublié ---------------------------------------------------
def _lien_de_reinit(client, email="a@exemple.fr"):
    """Demande un lien et récupère le jeton émis pour le compte."""
    client.post("/mot-de-passe-oublie", data={"email": email})
    utilisateur = users.par_email(email)
    return users.creer_jeton_reinit(utilisateur.id) if utilisateur else None


def test_la_demande_ne_revele_pas_qui_est_inscrit(client):
    inscrire(client)
    client.cookies.clear()
    connu = client.post("/mot-de-passe-oublie", data={"email": "a@exemple.fr"})
    inconnu = client.post("/mot-de-passe-oublie", data={"email": "z@exemple.fr"})
    assert connu.status_code == inconnu.status_code == 200
    # Même page, mot pour mot : rien ne distingue les deux cas.
    assert connu.text == inconnu.text


def test_le_lien_de_reinitialisation_ouvre_la_session(client):
    inscrire(client)
    client.cookies.clear()
    jeton = _lien_de_reinit(client)

    assert client.get(f"/reinitialiser?jeton={jeton}").status_code == 200
    reponse = client.post("/reinitialiser",
                          data={"jeton": jeton, "mot_de_passe": "nouveaumdp1"},
                          follow_redirects=False)
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/studio"
    assert users.SESSION_COOKIE in reponse.cookies
    assert client.get("/studio").status_code == 200

    client.cookies.clear()
    assert client.post("/connexion", data={"email": "a@exemple.fr",
                                           "mot_de_passe": "nouveaumdp1"},
                       follow_redirects=False).status_code == 303


def test_un_lien_ne_sert_qu_une_fois(client):
    inscrire(client)
    client.cookies.clear()
    jeton = _lien_de_reinit(client)
    client.post("/reinitialiser", data={"jeton": jeton,
                                        "mot_de_passe": "nouveaumdp1"},
                follow_redirects=False)
    client.cookies.clear()

    # Changer le mot de passe invalide le lien déjà utilisé.
    assert client.get(f"/reinitialiser?jeton={jeton}").status_code == 400
    rejoue = client.post("/reinitialiser",
                         data={"jeton": jeton, "mot_de_passe": "encoreautre1"},
                         follow_redirects=False)
    assert rejoue.status_code == 400
    with pytest.raises(users.CompteError):
        users.authentifier("a@exemple.fr", "encoreautre1")


def test_un_jeton_falsifie_ou_perime_est_refuse(client, monkeypatch):
    inscrire(client)
    client.cookies.clear()
    jeton = _lien_de_reinit(client)

    assert users.lire_jeton_reinit("") is None
    assert users.lire_jeton_reinit("nimporte.quoi") is None
    assert users.lire_jeton_reinit(jeton[:-2] + "AA") is None   # signature cassée
    assert client.get("/reinitialiser?jeton=bidon").status_code == 400

    # Une heure plus tard, le lien ne vaut plus rien.
    maintenant = users.time.time
    monkeypatch.setattr(users.time, "time",
                        lambda: maintenant() + users.REINIT_MINUTES * 60 + 1)
    assert users.lire_jeton_reinit(jeton) is None


def test_un_jeton_de_reinit_ne_sert_pas_de_cookie_de_session(client):
    inscrire(client)
    identifiant = users.par_email("a@exemple.fr").id
    client.cookies.clear()

    jeton = users.creer_jeton_reinit(identifiant)
    assert users.lire_session(jeton) is None        # marqueur distinct
    session = users.creer_session(identifiant)
    assert users.lire_jeton_reinit(session) is None  # et l'inverse

    client.cookies.set(users.SESSION_COOKIE, jeton)
    assert client.get("/studio", follow_redirects=False).status_code == 303


def test_la_page_dit_ou_trouver_le_lien_sans_smtp(client, monkeypatch):
    """Sans SMTP configuré, on l'annonce plutôt que de faire croire à un envoi."""
    inscrire(client)
    client.cookies.clear()
    monkeypatch.setattr(app_module.courriel, "configure", lambda: False)
    page = client.post("/mot-de-passe-oublie", data={"email": "a@exemple.fr"})
    assert page.status_code == 200
    assert "journal" in page.text.lower()
