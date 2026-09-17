"""La couleur de la marque doit vivre dans les tokens, et nulle part ailleurs.

Ces vérifications ne jugent pas du goût. Elles tiennent une promesse
mécanique : changer la couleur du site doit rester une poignée de lignes.

Elle avait été rompue sans que rien ne le signale. L'accent était recopié en
dur à cent soixante-sept endroits — à chaque fois avec une opacité différente,
ce qui empêchait de le factoriser tant qu'il était écrit en `rgba(r, g, b, a)`.
Le jour où il a fallu passer de l'orange au bleu, il a fallu tout relire, et
il en restait encore : le texte des boutons, le halo du fond, les icônes de
l'application, la moitié des teintes de repli.
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
STATIQUE = RACINE / "flambee" / "static"
FEUILLES = sorted(STATIQUE.glob("*.css"))

LITTERAL = re.compile(
    r"#([0-9a-fA-F]{6})\b"
    r"|rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})")

# Les couleurs qui ont le droit d'être écrites en clair : elles ne disent rien
# de la marque, elles disent un état. Un vert de réussite reste vert quelle que
# soit la couleur du site, et un rouge d'erreur reste rouge.
ETATS = {
    (95, 214, 164), (165, 234, 208),                    # réussite
    (255, 107, 107), (255, 122, 122), (255, 180, 180),  # erreur
    (255, 208, 208), (214, 69, 69),                     # erreur, nuances
}

# Au-delà de ce couple, une couleur est « franche » : on la voit, et elle
# appartient donc à l'identité. En deçà — un bleu presque noir, un blanc à
# peine teinté — c'est une encre ou du papier, pas un accent.
SATURATION = 0.45
LUMIERE = (0.20, 0.90)


def _litteraux(texte: str):
    """Les couleurs écrites en clair, avec leur ligne, hors commentaires."""
    sans_commentaires = re.sub(r"/\*.*?\*/", "", texte, flags=re.S)
    for numero, ligne in enumerate(sans_commentaires.splitlines(), 1):
        for m in LITTERAL.finditer(ligne):
            if m.group(1):
                rvb = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
            else:
                rvb = tuple(int(m.group(i)) for i in (2, 3, 4))
            yield numero, rvb, ligne.strip()


def test_aucune_couleur_de_marque_ecrite_en_dur():
    """Tout ce qui se voit passe par un token, sauf les couleurs d'état."""
    fautes = []
    for feuille in FEUILLES:
        for numero, rvb, ligne in _litteraux(feuille.read_text(encoding="utf-8")):
            if rvb in ETATS:
                continue
            _, lumiere, saturation = colorsys.rgb_to_hls(*(c / 255 for c in rvb))
            if saturation >= SATURATION and LUMIERE[0] <= lumiere <= LUMIERE[1]:
                fautes.append(f"{feuille.name}:{numero}  rgb{rvb}  {ligne[:70]}")
    assert not fautes, (
        "couleurs franches écrites en clair — elles devraient passer par un "
        "token de :root, sans quoi le prochain changement de palette les "
        "oubliera :\n  " + "\n  ".join(fautes))


def test_le_nuanceur_et_la_feuille_de_style_disent_la_meme_couleur():
    """Le fond WebGL doit recopier l'accent : il ne lit pas les variables CSS.

    C'est le seul endroit du projet où la couleur est écrite deux fois. Une
    duplication qu'on ne peut pas supprimer se surveille.
    """
    style = (STATIQUE / "style.css").read_text(encoding="utf-8")
    nuanceur = (STATIQUE / "braises.js").read_text(encoding="utf-8")

    def token(nom: str) -> tuple[int, int, int]:
        m = re.search(rf"--{nom}:\s*([\d\s]+);", style)
        assert m, f"token --{nom} introuvable dans style.css"
        return tuple(int(v) for v in m.group(1).split())

    def couleur_glsl(nom: str) -> tuple[float, float, float]:
        m = re.search(rf"vec3 {nom}\s*=\s*vec3\(([^)]+)\)", nuanceur)
        assert m, f"couleur « {nom} » introuvable dans braises.js"
        return tuple(float(v) for v in m.group(1).split(","))

    for nom_css, nom_glsl in (("flamme-rvb", "feu"),
                              ("flamme-claire-rvb", "eclat")):
        attendu = tuple(round(c / 255, 3) for c in token(nom_css))
        obtenu = couleur_glsl(nom_glsl)
        assert obtenu == attendu, (
            f"braises.js `{nom_glsl}` vaut {obtenu}, la feuille de style dit "
            f"{attendu} pour --{nom_css} — le fond animé ne serait plus de la "
            f"couleur du site")


def test_l_icone_de_l_application_suit_la_palette():
    """Les trois PNG de l'écran d'accueil sont engendrés, pas dessinés.

    Ils sont restés orange une palette entière parce qu'ils étaient des
    fichiers déposés là, que rien ne reliait au reste.
    """
    import sys
    sys.path.insert(0, str(RACINE / "outils"))
    import engendrer_icones

    style = (STATIQUE / "style.css").read_text(encoding="utf-8")
    for nom, valeur in (("flamme-rvb", engendrer_icones.FLAMME_ACCENT),
                        ("flamme-claire-rvb", engendrer_icones.FLAMME_CLAIRE),
                        ("flamme-nuit-rvb", engendrer_icones.FLAMME_NUIT)):
        m = re.search(rf"--{nom}:\s*([\d\s]+);", style)
        attendu = "#%02x%02x%02x" % tuple(int(v) for v in m.group(1).split())
        assert valeur.lower() == attendu, (
            f"engendrer_icones.py écrit {valeur} là où --{nom} vaut {attendu}")


def test_la_favicone_et_l_icone_dessinent_la_meme_flamme():
    """Deux tracés qui divergent, ce sont deux marques.

    La favicone est écrite en clair dans la page — elle doit l'être, sinon le
    navigateur ferait une requête de plus pour seize pixels — mais le tracé,
    lui, n'a aucune raison de différer de celui des icônes.
    """
    import sys
    sys.path.insert(0, str(RACINE / "outils"))
    import engendrer_icones

    base = (RACINE / "flambee" / "templates" / "site" / "base.html").read_text("utf-8")
    lignes = [l for l in base.splitlines() if 'rel="icon"' in l]
    assert len(lignes) == 1, f"{len(lignes)} favicones déclarées, une attendue"
    m = re.search(r"path fill='%23([0-9a-fA-F]{6})' d='([^']+)'", lignes[0])
    assert m, "aucun tracé dans la favicone"
    couleur, trace = "#" + m.group(1), m.group(2)

    assert trace == engendrer_icones.FLAMME, (
        "le tracé de la favicone a divergé de celui des icônes")
    assert couleur.lower() == engendrer_icones.FLAMME_ACCENT.lower(), (
        f"la favicone est en {couleur}, l'icône en "
        f"{engendrer_icones.FLAMME_ACCENT}")


def test_les_nuanceurs_ne_sont_pas_coupes_par_un_accent_grave():
    """Un accent grave dans un commentaire GLSL referme le gabarit JavaScript.

    Les deux nuanceurs sont écrits dans des `template literals` : le premier
    backquote rencontré à l'intérieur — fût-ce au milieu d'un commentaire —
    termine la chaîne, et la suite du fichier est lue comme du JavaScript.
    Tout `braises.js` cesse alors de s'analyser, sans que rien ne le signale :
    la page se charge, le champ de braises ne s'allume jamais. C'est arrivé au
    passage de l'orange au bleu, et c'est resté en ligne.

    On ne compte pas les backquotes — leur nombre était resté pair. On vérifie
    que chaque gabarit contient encore le corps qu'il est censé porter.
    """
    source = (RACINE / "flambee" / "static" / "braises.js").read_text(encoding="utf-8")
    for nom in ("SOMMET", "FRAGMENT"):
        debut = source.index(f"const {nom} = `") + len(f"const {nom} = `")
        fin = source.index("`", debut)
        # Chercher un marqueur du nuanceur ne suffit pas : `void main` se
        # trouve avant le commentaire fautif, et le test passait quand même.
        # Ce qui ne trompe pas, c'est ce qui suit le backquote fermant.
        suite = source[fin + 1:fin + 2]
        assert suite == ";", (
            f"le gabarit {nom} se referme sur « {source[fin:fin + 40]!r} » au "
            "lieu d'un point-virgule : un accent grave traîne dans le "
            "nuanceur, souvent au milieu d'un commentaire")
