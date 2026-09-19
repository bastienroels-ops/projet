"""Étape 4 — génération du script via l'API Claude.

Sans clé API (`ANTHROPIC_API_KEY`), l'application bascule en mode manuel :
elle affiche le prompt à copier dans une conversation Claude, et l'utilisateur
colle le script obtenu. Le reste du pipeline est identique.
"""

from __future__ import annotations

import logging
import os
import re

from . import config

log = logging.getLogger(__name__)

MIN_WORDS = 60    # ~30 s de voix off
MAX_WORDS = 160   # ~60 s de voix off


class ScriptError(RuntimeError):
    """La génération du script a échoué."""


def api_key_available() -> bool:
    return bool(os.environ.get(config.ANTHROPIC_API_KEY_ENV, "").strip())


def build_user_prompt(topic: str, instructions: str = "", duration: int = 45) -> str:
    """Construit le message utilisateur envoyé à Claude (ou copié à la main)."""
    topic = topic.strip()
    if not topic:
        raise ScriptError("Indique le sujet de la vidéo.")

    parts = [
        f"Sujet de la vidéo : {topic}",
        f"Durée visée : environ {duration} secondes de voix off "
        f"(soit {int(duration * 2.6)} à {int(duration * 3.2)} mots).",
    ]
    if instructions.strip():
        parts.append(f"Consignes supplémentaires : {instructions.strip()}")
    parts.append(
        "Réponds uniquement avec le texte du script, sans titre, sans "
        "commentaire, sans indication de section."
    )
    return "\n".join(parts)


def full_prompt_for_copy(topic: str, instructions: str = "", duration: int = 45) -> str:
    """Prompt complet (système + utilisateur) pour le mode manuel."""
    return (
        f"{config.SCRIPT_SYSTEM_PROMPT}\n\n---\n\n"
        f"{build_user_prompt(topic, instructions, duration)}"
    )


def generate(
    topic: str,
    instructions: str = "",
    *,
    duration: int = 45,
    model: str | None = None,
) -> str:
    """Appelle l'API Claude et retourne le script généré."""
    if not api_key_available():
        raise ScriptError(
            "Aucune clé API trouvée. Définis ANTHROPIC_API_KEY, ou utilise le "
            "mode manuel (copie le prompt dans une conversation Claude)."
        )

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise ScriptError("Le paquet `anthropic` n'est pas installé.") from exc

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=model or config.ANTHROPIC_MODEL,
            max_tokens=1200,
            temperature=1.0,
            system=config.SCRIPT_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_user_prompt(topic, instructions, duration),
                }
            ],
        )
    except Exception as exc:
        raise ScriptError(f"Appel à l'API Claude impossible : {exc}") from exc

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    script = tidy(text)
    if not script:
        raise ScriptError("Claude a renvoyé une réponse vide.")
    return script


def inspirer_depuis_tendance(caracteristiques: dict, *, model: str | None = None) -> str:
    """Une idée de contenu originale inspirée des caractéristiques d'une
    tendance analysée — jamais une reprise de son texte.

    `caracteristiques` vient de `trends.AnalyseVideo.to_dict()["analyse"]` :
    seuls le sujet, l'amorce, le CTA, les hashtags et le rythme y passent —
    jamais la transcription intégrale, pour qu'il n'y ait rien à recopier.
    Même mécanique que `generate()` : nécessite `ANTHROPIC_API_KEY`.
    """
    if not api_key_available():
        raise ScriptError(
            "Aucune clé API trouvée. Définis ANTHROPIC_API_KEY pour utiliser "
            "l'inspiration automatique."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise ScriptError("Le paquet `anthropic` n'est pas installé.") from exc

    parts = []
    if caracteristiques.get("sujet"):
        parts.append(f"Sujet observé : {caracteristiques['sujet']}")
    if caracteristiques.get("hook_extrait"):
        parts.append(f"Amorce observée : {caracteristiques['hook_extrait']}")
    if caracteristiques.get("cta_detecte"):
        parts.append(f"Appel à l'action observé : {caracteristiques['cta_detecte']}")
    if caracteristiques.get("hashtags"):
        parts.append(f"Hashtags : {', '.join(caracteristiques['hashtags'][:8])}")
    if caracteristiques.get("rythme_coupes_par_minute"):
        parts.append(
            f"Rythme : environ {caracteristiques['rythme_coupes_par_minute']:.0f} "
            "changements de plan par minute"
        )
    contexte = "\n".join(parts) or "Aucune caractéristique détaillée disponible."

    system = (
        "Tu aides un créateur à s'inspirer d'une tendance TikTok pour produire "
        "une idée de vidéo ORIGINALE, sur sa propre thématique. Tu ne dois "
        "jamais reprendre le texte, les mots ou la structure exacte de la "
        "vidéo observée : tu t'appuies sur ce qui explique son succès (l'angle "
        "du hook, le rythme, le type d'appel à l'action) pour proposer un "
        "concept neuf. Réponds en français, avec : un titre court, l'angle du "
        "hook proposé, un résumé du déroulé en trois phrases, et une phrase "
        "sur pourquoi cet angle peut fonctionner. Pas de markdown, du texte "
        "simple avec des retours à la ligne entre les parties."
    )
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=model or config.ANTHROPIC_MODEL,
            max_tokens=700,
            temperature=1.0,
            system=system,
            messages=[{
                "role": "user",
                "content": (
                    "Caractéristiques observées sur une vidéo tendance (pas "
                    f"son texte intégral) :\n{contexte}\n\nPropose une idée "
                    "originale qui s'en inspire."
                ),
            }],
        )
    except Exception as exc:
        raise ScriptError(f"Appel à l'API Claude impossible : {exc}") from exc

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    idee = tidy(text)
    if not idee:
        raise ScriptError("Claude a renvoyé une réponse vide.")
    return idee


def tidy(text: str) -> str:
    """Retire les scories courantes autour d'un script généré."""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text)
    text = re.sub(r"^\s*(voici|bien sûr)[^\n:]*:\s*\n", "", text,
                  flags=re.IGNORECASE)
    return text.strip()


def estimate_duration(script: str, words_per_minute: float = 170.0) -> float:
    """Estime la durée de lecture d'un script, en secondes."""
    words = len([w for w in re.split(r"\s+", script.strip()) if w])
    return words / words_per_minute * 60 if words else 0.0


def review(script: str) -> list[str]:
    """Petits garde-fous affichés sous le script dans l'interface."""
    notes: list[str] = []
    words = len([w for w in re.split(r"\s+", script.strip()) if w])
    if not words:
        return ["Le script est vide."]
    if words < MIN_WORDS:
        notes.append(f"Script court ({words} mots) : vidéo de moins de 30 s.")
    if words > MAX_WORDS:
        notes.append(f"Script long ({words} mots) : vidéo de plus de 60 s.")
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", script):
        notes.append("Des emojis sont présents : ils seront lus ou ignorés par la voix.")
    return notes
