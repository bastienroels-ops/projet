"""Jeu d'icônes de Flambée.

Les emoji rendaient chaque interface différente : leur dessin appartient au
système d'exploitation, pas au site. Sur un iPhone, un Android et un poste
Linux, la même page n'avait ni le même trait, ni la même couleur, ni le même
poids visuel — et dans une colonne de navigation, ce désordre se voit.

Ces icônes sont donc dessinées ici, en SVG, avec une grammaire unique : une
grille de 24, un trait de 1,6, des extrémités arrondies, et la couleur du
texte environnant (`currentColor`). Elles s'intègrent au flux du document, se
colorent par CSS et ne coûtent aucune requête.
"""

from __future__ import annotations

from markupsafe import Markup

# Chaque entrée est le contenu du <svg> : uniquement la géométrie.
_TRACES: dict[str, str] = {
    # --- Le produit -------------------------------------------------------
    "ciseaux": (
        '<circle cx="6" cy="6" r="2.6"/><circle cx="6" cy="18" r="2.6"/>'
        '<path d="M20.5 3.5 8.4 15.6M20.5 20.5 8.4 8.4"/>'
    ),
    "eclair": '<path d="M13.2 2.5 4.8 13.4h6.2l-1.2 8.1 8.4-10.9h-6.2l1.2-8.1Z"/>',
    "sous-titres": (
        '<rect x="2.6" y="4.8" width="18.8" height="14.4" rx="3.2"/>'
        '<path d="M6.4 11.4h6.2M15.2 11.4h2.4M6.4 15.2h3.4M12.2 15.2h5.4"/>'
    ),
    "curseurs": (
        '<path d="M6 3v5.2M6 12.8V21M12 3v8.4M12 16v5M18 3v2.6M18 10.2V21"/>'
        '<circle cx="6" cy="10.5" r="2.1"/><circle cx="12" cy="13.7" r="2.1"/>'
        '<circle cx="18" cy="7.9" r="2.1"/>'
    ),
    "engrenage": (
        '<path d="M10.2 2.4h3.6l.5 2.4 1.7 1 2.3-.9 1.8 3.1-1.8 1.7v2l1.8 1.7'
        '-1.8 3.1-2.3-.9-1.7 1-.5 2.4h-3.6l-.5-2.4-1.7-1-2.3.9L4 15.4l1.8-1.7'
        'v-2L4 10l1.7-3.1 2.3.9 1.7-1 .5-2.4Z"/>'
        '<circle cx="12" cy="12" r="2.9"/>'
    ),
    "telephone": (
        '<rect x="6" y="2.3" width="12" height="19.4" rx="3.2"/>'
        '<path d="M10.4 18.6h3.2"/>'
    ),

    # --- L'atelier --------------------------------------------------------
    "document": (
        '<path d="M13.8 2.6H7.6A2.6 2.6 0 0 0 5 5.2v13.6a2.6 2.6 0 0 0 2.6 2.6h8.8'
        'a2.6 2.6 0 0 0 2.6-2.6V7.4l-5.2-4.8Z"/>'
        '<path d="M13.6 2.8v4.2a.6.6 0 0 0 .6.6h4.4"/>'
        '<path d="M8.6 13h6.8M8.6 16.8h4.4"/>'
    ),
    "micro": (
        '<rect x="9" y="2.4" width="6" height="11.2" rx="3"/>'
        '<path d="M5.4 11.4a6.6 6.6 0 0 0 13.2 0M12 18v3.6M9.2 21.6h5.6"/>'
    ),
    "film": (
        '<rect x="2.6" y="4.6" width="18.8" height="14.8" rx="2.8"/>'
        '<path d="M2.6 9.2h18.8M2.6 14.8h18.8M7.8 4.6v14.8M16.2 4.6v14.8"/>'
    ),
    "livre": (
        '<path d="M4.4 4.2A2.4 2.4 0 0 1 6.8 1.8h12.8v16.4H6.8a2.4 2.4 0 0 0-2.4 2.4V4.2Z"/>'
        '<path d="M4.4 20.6a2.4 2.4 0 0 1 2.4-2.4h12.8v4H6.8a2.4 2.4 0 0 1-2.4-1.6Z"/>'
        '<path d="M8.6 6.4h6.8M8.6 10h4.4"/>'
    ),
    "communaute": (
        '<circle cx="9.2" cy="8" r="3.3"/>'
        '<path d="M2.8 20.2c0-3.4 2.9-5.7 6.4-5.7s6.4 2.3 6.4 5.7"/>'
        '<path d="M16.4 5.3a3.3 3.3 0 0 1 0 5.4M18.2 14.9c1.9.9 3.1 2.7 3.1 5.3"/>'
    ),
    "carte": (
        '<rect x="2.4" y="5" width="19.2" height="14" rx="2.8"/>'
        '<path d="M2.4 9.6h19.2M6.4 14.8h3.4"/>'
    ),
    "jeton": (
        '<circle cx="12" cy="12" r="8.6"/>'
        '<path d="M12 7.2v9.6"/>'
        '<path d="M14.4 9.6c-.6-.8-1.5-1.2-2.4-1.2-1.4 0-2.5.8-2.5 1.9 0 1 .8 1.6 '
        '2.5 1.9 1.7.3 2.5.9 2.5 1.9 0 1.1-1.1 1.9-2.5 1.9-1 0-1.8-.4-2.4-1.2"/>'
    ),
    "reglages": (
        '<path d="M3.4 7.2h8.2M17.4 7.2h3.2M3.4 16.8h3.2M12.4 16.8h8.2"/>'
        '<circle cx="14.5" cy="7.2" r="2.3"/><circle cx="9.5" cy="16.8" r="2.3"/>'
    ),
    "profil": (
        '<circle cx="12" cy="8" r="3.7"/>'
        '<path d="M4.6 20.6c0-3.9 3.3-6.6 7.4-6.6s7.4 2.7 7.4 6.6"/>'
    ),

    # --- États et formules ------------------------------------------------
    "cadenas": (
        '<rect x="4.4" y="10" width="15.2" height="11.2" rx="2.8"/>'
        '<path d="M7.8 10V7.2a4.2 4.2 0 0 1 8.4 0V10"/><path d="M12 14.6v2.4"/>'
    ),
    "etoile": (
        '<path d="m12 3 2.75 5.6 6.18.9-4.47 4.36 1.06 6.16L12 17.11 '
        '6.48 20.02l1.06-6.16L3.07 9.5l6.18-.9L12 3Z"/>'
    ),
    "diamant": (
        '<path d="M6.6 3h10.8l4.1 6L12 21 1.5 9l5.1-6Z"/>'
        '<path d="M1.5 9h20M8.6 3l3.4 6 3.4-6M12 9v12"/>'
    ),
    "plus": '<path d="M12 5.2v13.6M5.2 12h13.6"/>',
    "lecture": '<path d="M8 5.4 18.4 12 8 18.6V5.4Z"/>',

    # La flamme est pleine : c'est une marque, pas un pictogramme.
    "flamme": (
        '<path d="M12 21.6c3.7 0 6.7-2.8 6.7-6.4 0-4.8-4.2-6.7-4.2-11.2 0 0-3.1 '
        '1.7-3.1 5.5 0 1.7-1 2.4-1.9 1.7-.7-.6-.9-1.9-.9-1.9S5.3 11.2 5.3 15.2'
        'c0 3.6 3 6.4 6.7 6.4Z" fill="currentColor" stroke="none"/>'
    ),
}

ICONES = tuple(sorted(_TRACES))


def icone(nom: str, *, taille: int = 20, classe: str = "") -> Markup:
    """Rend une icône en SVG, prête à poser dans un gabarit.

    `aria-hidden` est systématique : ces icônes accompagnent toujours un
    libellé écrit. Une icône annoncée deux fois — par son dessin et par le mot
    d'à côté — encombre la lecture à voix haute sans rien apporter.
    """
    trace = _TRACES.get(nom)
    if trace is None:
        raise KeyError(f"Icône inconnue : {nom}")
    classes = f"icone {classe}".strip()
    return Markup(
        f'<svg class="{classes}" width="{taille}" height="{taille}" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{trace}</svg>'
    )
