"""Tests de l'application connectée : coquille, rubriques, crédits, voix."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from flambee import account, app as app_module, media, plans, users  # noqa: E402
from flambee import voicestudio  # noqa: E402


RUBRIQUES = ["/studio", "/studio/creations", "/studio/script-viral", "/studio/voix",
             "/studio/tutoriel", "/studio/communaute", "/studio/abonnement",
             "/studio/credits", "/studio/parametres", "/studio/profil"]


@pytest.mark.parametrize("chemin", RUBRIQUES)
def test_chaque_rubrique_repond(compte, chemin):
    reponse = compte.get(chemin)
    assert reponse.status_code == 200
    # Le menu latéral est présent sur toutes les pages.
    assert 'href="/studio/abonnement"' in reponse.text
    assert 'class="entree creer' in reponse.text


def test_latelier_garde_tous_ses_rouages(compte):
    """L'outil a changé de gabarit : ses identifiants doivent survivre."""
    texte = compte.get("/studio").text
    for identifiant in ("urls", "btn-download", "hooks", "subtitle_preset",
                        "script", "btn-render", "result-video", "preset-video",
                        # La refonte de l'étape Style : les cartes pilotent
                        # ces listes, qui restent la source de vérité.
                        "voice", "music", "subtitles", "choix-voix",
                        "choix-musique", "choix-soustitres", "jauge-script"):
        assert f'id="{identifiant}"' in texte, identifiant
    assert "/static/app.js" in texte


def test_la_jauge_du_script_porte_les_bornes_du_moteur(compte):
    """La fenêtre visée — 60 à 160 mots — est une règle de `scriptgen`. Si la
    jauge la recopiait dans le JavaScript, les deux dériveraient au premier
    réglage : on vérifie que la page tient ses chiffres du moteur."""
    from flambee import scriptgen

    texte = compte.get("/studio").text
    assert f'data-min="{scriptgen.MIN_WORDS}"' in texte
    assert f'data-max="{scriptgen.MAX_WORDS}"' in texte


def test_les_icones_des_cartes_viennent_du_jeu_du_serveur(compte):
    """Le script pose trois icônes sur les cartes qu'il fabrique. Le gabarit
    les lui fournit : redessinées en JavaScript, elles vivraient à deux
    endroits."""
    texte = compte.get("/studio").text
    for nom in ("lecture", "pause", "cadenas"):
        assert f'data-icone="{nom}"' in texte, nom
    assert "<svg" in texte.split('data-icone="lecture"')[1][:200]


def test_l_attribut_hidden_l_emporte_sur_la_feuille_de_style():
    """Sans cette règle, `hidden` ne masque rien dès que l'élément porte une
    classe qui pose un `display`.

    L'attribut vient de la feuille par défaut du navigateur : la moindre règle
    d'auteur la bat. Le panneau de l'écran scindé portait `class="grid"` et
    `hidden` en même temps — il restait à l'écran, l'attribut correctement posé
    par le script et sans le moindre effet. Le piège guette tout bloc masqué
    par attribut : `.checks`, `.choix`, `.grid`.
    """
    import re

    feuille = (RACINE / "flambee" / "static" / "style.css").read_text(
        encoding="utf-8")
    # On cherche la règle, pas la chaîne : le commentaire qui l'explique cite
    # « [hidden] » lui aussi, et un test qui tombe dessus ne prouve rien.
    regle = re.search(r"^\[hidden\]\s*\{([^}]*)\}", feuille, re.MULTILINE)
    assert regle, "la règle [hidden] a disparu de la feuille de style"
    assert "!important" in regle.group(1), (
        "sans !important, `.grid` — même spécificité, plus bas dans le "
        "fichier — reprend la main et l'attribut ne masque plus rien")


def test_la_rubrique_active_est_signalee(compte):
    assert 'class="entree active" href="/studio/credits"' in \
        compte.get("/studio/credits").text.replace("\n", " ")


# --- Crédits et formules ---------------------------------------------------
def test_le_quota_depend_de_la_formule(compte):
    assert "3" in compte.get("/studio/credits").text          # essai : 3 rendus

    reponse = compte.post("/studio/abonnement", data={"plan": "studio"},
                          follow_redirects=True)
    assert reponse.status_code == 200
    utilisateur = users.par_email("essai@exemple.fr")
    assert utilisateur.plan == "studio"
    assert account.quota(utilisateur) is None                 # illimité


def test_un_rendu_consomme_un_credit(compte):
    utilisateur = users.par_email("essai@exemple.fr")
    depart = account.restants(utilisateur)
    account.noter(utilisateur, "rendu", "video.mp4")
    assert account.restants(users.par_email("essai@exemple.fr")) == depart - 1
    assert "Vidéo rendue" in compte.get("/studio/credits").text


def test_rendu_refuse_sans_credit(compte, monkeypatch):
    """Les aperçus restent ouverts, le rendu définitif non."""
    utilisateur = users.par_email("essai@exemple.fr")
    for _ in range(3):
        account.noter(utilisateur, "rendu", "x")
    assert account.peut_rendre(users.par_email("essai@exemple.fr")) is False

    projet = compte.post("/api/projects").json()
    reponse = compte.post(f"/api/projects/{projet['id']}/render", json={"fast": False})
    assert reponse.status_code == 402
    assert "Crédits épuisés" in reponse.json()["detail"]


def test_les_apercus_ne_consomment_rien(compte, monkeypatch):
    utilisateur = users.par_email("essai@exemple.fr")
    for _ in range(5):
        account.noter(utilisateur, "apercu", "x")
    assert account.restants(users.par_email("essai@exemple.fr")) == 3


# --- Rubriques réservées ---------------------------------------------------
def test_script_viral_verrouille_en_essai(compte):
    texte = compte.get("/studio/script-viral").text
    assert "Disponible à partir de" in texte
    assert "PRO" in texte

    reponse = compte.post("/studio/script-viral", data={"url": "https://a.test/1"})
    assert reponse.status_code == 402


def test_script_viral_ouvert_en_createur(compte_pro):
    texte = compte_pro.get("/studio/script-viral").text
    assert "Disponible à partir de" not in texte


def test_voice_studio_refuse_un_format_inconnu(compte_pro):
    reponse = compte_pro.post("/studio/voix",
                              files={"fichier": ("note.txt", b"abc", "text/plain")})
    assert reponse.status_code == 400
    assert "Format non reconnu" in reponse.text


def test_profil_enregistre_le_nom(compte):
    compte.post("/studio/profil", data={"nom": "Bastien"}, follow_redirects=True)
    utilisateur = users.par_email("essai@exemple.fr")
    assert utilisateur.nom == "Bastien"
    assert utilisateur.initiales == "BA"


# --- Voix importée ---------------------------------------------------------
def test_la_voix_importee_apparait_dans_la_liste(compte, monkeypatch):
    assert all(v["id"] != voicestudio.VOICE_ID
               for v in compte.get("/api/voices").json()["voices"])

    monkeypatch.setattr(voicestudio, "charger",
                        lambda owner=0: {"nom": "moi.m4a", "mots": []})
    voix = compte.get("/api/voices").json()["voices"]
    assert voix[0]["id"] == voicestudio.VOICE_ID     # proposée en premier


def test_fichier_de_voix_absent(compte):
    assert compte.get("/studio/voix/fichier").status_code == 404


def test_la_vignette_d_un_rendu_est_une_image(compte, tmp_path):
    """Quarante lecteurs vidéo rendraient la liste des créations inutilisable
    sur une connexion mobile ; une image de quelques kilo-octets suffit."""
    import subprocess

    from flambee import media
    from flambee.project import store

    if media.ensure_tools():
        pytest.skip("ffmpeg requis")

    projet = store.create(compte_id := _identifiant(compte))
    sortie = projet.dir / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=540x960:duration=3:rate=25",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(sortie)],
                   check=True, capture_output=True)
    projet.output_path = str(sortie)
    projet.save()

    reponse = compte.get(f"/api/projects/{projet.id}/poster")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "image/jpeg"
    assert len(reponse.content) > 800

    # Le second appel vient du cache, sans relancer ffmpeg.
    empreintes = list((projet.dir / ".viewing").glob("affiche-*.jpg"))
    assert len(empreintes) == 1


def test_un_projet_sans_rendu_n_a_pas_de_vignette(compte):
    from flambee.project import store

    projet = store.create(_identifiant(compte))
    assert compte.get(f"/api/projects/{projet.id}/poster").status_code == 404


def _identifiant(client) -> int:
    from flambee import users

    return users.par_email("essai@exemple.fr").id

