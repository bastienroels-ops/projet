"""Étape 5b — sous-titres animés au format ASS.

Le rendu reprend le style « mot par mot » des vidéos virales : la ligne
complète reste affichée, le mot prononcé est mis en couleur et légèrement
agrandi, avec un petit effet de pop à chaque nouvelle ligne.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import config
from .media import split_bands
from .voice import Word

_ASS_HEADER = """[Script Info]
; Généré par Flambée
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Flambee,{font},{size},{primary},{highlight},{outline_color},{back},{bold},0,0,0,100,100,{spacing},0,{border_style},{outline},{shadow},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_SENTENCE_END = re.compile(r"[.!?…:;]$")

LINE_HOLD = 0.18   # temps de lecture accordé après le dernier mot d'une ligne
LINE_GAP = 0.02    # écart minimal entre deux lignes, pour ne jamais les superposer
_LEADING_PUNCT = re.compile(r"^[,;:.!?…»\)\]]+")


@dataclass
class Line:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def group_words(
    words: list[Word],
    style: config.SubtitleStyle | None = None,
) -> list[Line]:
    """Regroupe les mots en lignes courtes, en coupant sur la ponctuation."""
    style = style or config.SUBTITLE_STYLE
    lines: list[Line] = []
    current: list[Word] = []
    length = 0

    for word in words:
        token = word.text.strip()
        # edge-tts rattache parfois la ponctuation au mot suivant (« ,Tu ») :
        # on la recolle au mot précédent.
        leading = _LEADING_PUNCT.match(token)
        if leading and current:
            punct = leading.group().strip()
            token = token[leading.end():].strip()
            current[-1].text += punct
            length += len(punct)
            if _SENTENCE_END.search(punct):     # la phrase se termine ici
                lines.append(Line(current))
                current, length = [], 0
        if not token:
            continue
        extra = len(token) + (1 if current else 0)
        too_long = current and (
            length + extra > style.max_chars_per_line
            or len(current) >= style.max_words_per_line
        )
        # Une coupure franche (>0.45 s) marque souvent une nouvelle phrase.
        gap = current and (word.start - current[-1].end) > 0.45
        if too_long or gap:
            lines.append(Line(current))
            current, length = [], 0
            extra = len(token)

        current.append(Word(text=token, start=word.start, end=word.end))
        length += extra

        if _SENTENCE_END.search(token):
            lines.append(Line(current))
            current, length = [], 0

    if current:
        lines.append(Line(current))
    return lines


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
    secs, cs = divmod(rest, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _inline_color(ass_color: str) -> str:
    """Convertit `&H00BBGGRR` (style) en `&HBBGGRR&` (tag inline)."""
    value = ass_color.replace("&H", "").replace("&", "")
    return f"&H{value[-6:]}&"


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
    )


def build_ass(
    words: list[Word],
    *,
    style: config.SubtitleStyle | None = None,
    fmt: config.VideoFormat | None = None,
    offset: float = 0.0,
    max_duration: float | None = None,
) -> str:
    """Construit le contenu d'un fichier `.ass` à partir du minutage des mots."""
    style = style or config.SUBTITLE_STYLE
    fmt = fmt or config.FORMAT

    header = _ASS_HEADER.format(
        width=fmt.width,
        height=fmt.height,
        font=style.font,
        size=style.font_size,
        bold=style.bold,
        primary=style.primary_color,
        highlight=style.highlight_color,
        outline_color=style.outline_color,
        back=style.back_color,
        border_style=style.border_style,
        outline=style.outline,
        shadow=style.shadow,
        spacing=style.spacing,
        margin_v=style.margin_v,
    )

    highlight = _inline_color(style.highlight_color)
    normal = _inline_color(style.primary_color)
    events: list[str] = []

    lines = group_words(words, style)
    for index, line in enumerate(lines):
        line_start = line.start + offset
        line_end = line.end + offset
        if max_duration is not None:
            if line_start >= max_duration:
                break
            line_end = min(line_end, max_duration)

        # On laisse la ligne un court instant de plus à l'écran, mais jamais
        # au-delà du début de la suivante : deux lignes affichées en même temps
        # se superposent à l'image, et le texte devient illisible. À l'intérieur
        # d'une phrase, les lignes s'enchaînent sans pause — le cas est donc la
        # règle, pas l'exception.
        line_end += LINE_HOLD
        if index + 1 < len(lines):
            line_end = min(line_end, lines[index + 1].start + offset - LINE_GAP)
        if max_duration is not None:
            line_end = min(line_end, max_duration)
        if line_end <= line_start:
            continue

        glow = f"\\blur{style.glow}" if style.glow else ""

        if not style.animate:
            events.append(_dialogue(
                line_start, line_end,
                "{\\fad(120,120)" + glow + "}" + _escape(_case(line.text, style)),
            ))
            continue

        for position, word in enumerate(line.words):
            start = max(line_start, word.start + offset)
            end = word.end + offset
            if position == len(line.words) - 1:
                end = line_end
            else:
                end = max(end, line.words[position + 1].start + offset)
            if max_duration is not None:
                start, end = min(start, max_duration), min(end, max_duration)
            if end <= start:
                continue

            pieces = []
            for other, item in enumerate(line.words):
                token = _escape(_case(item.text, style))
                if other == position:
                    grossi = style.highlight_scale
                    échelle = (f"\\fscx{grossi}\\fscy{grossi}" if grossi != 100 else "")
                    pieces.append(
                        f"{{\\1c{highlight}{échelle}}}{token}"
                        f"{{\\1c{normal}\\fscx100\\fscy100}}"
                    )
                else:
                    pieces.append(token)
            text = " ".join(pieces)

            # Pop uniquement sur le premier mot de la ligne : l'effet reste lisible.
            prefix = (
                "{\\fad(60,0)" + glow
                + "\\t(0,110,\\fscx106\\fscy106)\\t(110,200,\\fscx100\\fscy100)}"
                if position == 0
                else "{" + glow + "}" if glow else ""
            )
            events.append(_dialogue(start, end, prefix + text))

    return header + "\n".join(events) + "\n"


def _case(text: str, style: config.SubtitleStyle) -> str:
    """Applique la casse du style (certains rendus vivent en capitales)."""
    return text.upper() if style.uppercase else text


def _dialogue(start: float, end: float, text: str) -> str:
    # 10 champs : Layer, Start, End, Style, Name, MarginL, MarginR, MarginV,
    # Effect, Text — l'en-tête [Events] doit les déclarer dans le même ordre,
    # sans quoi le champ en trop se retrouve collé au début du texte affiché.
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Flambee,,0,0,0,,{text}"


def placer(style: config.SubtitleStyle, reglages: config.RenderSettings,
           fmt: config.VideoFormat) -> config.SubtitleStyle:
    """Applique la hauteur choisie, puis la contrainte de l'écran scindé.

    Dans cet ordre, et pas l'inverse : l'écran scindé n'est pas une
    préférence, c'est une place disponible. Un texte posé trop bas y
    tomberait dans la vidéo du bas, et aucun réglage ne doit pouvoir le
    demander.
    """
    return style_pour_ecran_scinde(
        replace(style, margin_v=hauteur_en_pixels(reglages, fmt)),
        reglages, fmt)


def hauteur_en_pixels(reglages: config.RenderSettings,
                      fmt: config.VideoFormat) -> int:
    """La hauteur du texte en pixels, depuis le bas de l'image."""
    part = max(0.10, min(0.50, reglages.subtitle_position))
    return int(round(fmt.height * part))


def style_pour_ecran_scinde(style: config.SubtitleStyle,
                            reglages: config.RenderSettings,
                            fmt: config.VideoFormat) -> config.SubtitleStyle:
    """Remonte les sous-titres au-dessus de la couture, si couture il y a.

    Le compagnon occupe le bas du cadre. Avec sa marge d'origine — 300 à
    470 pixels selon le style — le texte tomberait au milieu de la vidéo de
    jeu : illisible, et posé sur ce qui bouge le plus dans l'image. On le
    replace juste au-dessus de la couture, dans la bande du montage, à une
    demi-hauteur de caractère du bord.

    Quand le compagnon est en haut, rien à faire : le montage occupe déjà le
    bas du cadre, là où la marge d'origine place le texte.
    """
    if not reglages.split_clip or not reglages.split_bottom:
        return style
    _, compagnon = split_bands(fmt.height, reglages.split_ratio)
    return replace(style, margin_v=compagnon + int(style.font_size * 0.5))


def write_ass(words: list[Word], out_path: Path, **kwargs) -> Path:
    """Écrit le fichier `.ass` sur disque et retourne son chemin."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_ass(words, **kwargs), encoding="utf-8")
    return out_path
