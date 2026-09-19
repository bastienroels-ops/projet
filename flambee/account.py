"""Formule, quotas et crédits — rattachés à un compte.

Le compte lui-même vit dans `users.py` ; ce module ne porte que les règles
commerciales : ce que donne droit une formule, et ce qui a été consommé.
"""

from __future__ import annotations

import time

from . import config, plans, users, voicestudio
from .users import Utilisateur

# Combien de rendus par mois selon la formule. None = sans limite.
QUOTAS: dict[str, int | None] = {
    "essai": 3,
    "createur": 30,
    "studio": None,
}

# Les formules qui ouvrent Script Viral et Voice Studio.
FORMULES_PRO = {"createur", "studio"}


# --- Ce que chaque formule ouvre vraiment ----------------------------------
# Jusqu'ici, seuls Script Viral, Voice Studio et le quota mensuel dépendaient
# de la formule. Tout le reste de la page Tarifs annonçait des différences qui
# n'existaient nulle part dans le code : styles limités, filigrane, voix
# réservées, durée de conservation. Ces règles-là les rendent réelles.

# Deux styles suffisent à juger de l'outil sans déflorer le catalogue.
STYLES_ESSAI: tuple[str, ...] = ("punch", "minimal")

# Deux voix en Essai, nommées plutôt que comptées : « les deux premières »
# donnait deux voix féminines, ce que la phrase ci-dessous promettait déjà de
# ne pas faire. Et une liste nommée se contrôle côté serveur, ce qu'un nombre
# ne permettait pas.
VOIX_ESSAI: tuple[str, ...] = ("fr-FR-DeniseNeural", "fr-FR-HenriNeural")

# Combien de temps les projets sont conservés. None = sans effacement.
RETENTION_JOURS: dict[str, int | None] = {
    "essai": 7,
    "createur": 90,
    "studio": None,
}


def styles_autorises(utilisateur: Utilisateur) -> list[str]:
    """Les styles de sous-titres ouverts à ce compte."""
    if est_pro(utilisateur):
        return list(config.SUBTITLE_PRESETS)
    return [nom for nom in config.SUBTITLE_PRESETS if nom in STYLES_ESSAI]


def style_autorise(utilisateur: Utilisateur, preset: str) -> bool:
    return preset in styles_autorises(utilisateur)


def voix_autorisees(utilisateur: Utilisateur) -> list[str]:
    """Les voix de synthèse ouvertes à ce compte."""
    if est_pro(utilisateur):
        return [v["id"] for v in config.FRENCH_VOICES]
    return [v["id"] for v in config.FRENCH_VOICES if v["id"] in VOIX_ESSAI]


def voix_autorisee(utilisateur: Utilisateur, voix: str) -> bool:
    """La voix importée du Voice Studio n'existe que pour les formules pro."""
    if voix == voicestudio.VOICE_ID:
        return est_pro(utilisateur)
    return voix in voix_autorisees(utilisateur)


def debit_reglable(utilisateur: Utilisateur) -> bool:
    """Le réglage du débit de la voix est un raffinement, pas un essentiel."""
    return est_pro(utilisateur)


def musique_autorisee(utilisateur: Utilisateur) -> bool:
    """La musique de fond et son mixage sous la parole sont une finition."""
    return est_pro(utilisateur)


def tendances_transcription_autorisee(utilisateur: Utilisateur) -> bool:
    """La transcription complète d'une vidéo tendance (hook exact, appel à
    l'action fiable) demande le même moteur coûteux que Script Viral."""
    return est_pro(utilisateur)


def filigrane(utilisateur: Utilisateur) -> bool:
    """Les rendus de l'Essai portent une mention discrète."""
    return not est_pro(utilisateur)


def retention_jours(utilisateur: Utilisateur) -> int | None:
    return RETENTION_JOURS.get(utilisateur.plan, RETENTION_JOURS["essai"])


def formule(utilisateur: Utilisateur) -> plans.Plan:
    for plan in plans.PLANS:
        if plan.id == utilisateur.plan:
            return plan
    return plans.PLANS[0]


def est_pro(utilisateur: Utilisateur) -> bool:
    return utilisateur.plan in FORMULES_PRO


def quota(utilisateur: Utilisateur) -> int | None:
    return QUOTAS.get(utilisateur.plan, 3)


def periode() -> str:
    return time.strftime("%Y-%m")


def consommes(utilisateur: Utilisateur, mois: str | None = None) -> int:
    mois = mois or periode()
    return sum(1 for evenement in utilisateur.historique
               if evenement.get("type") == "rendu"
               and str(evenement.get("date", "")).startswith(mois))


def restants(utilisateur: Utilisateur) -> int | None:
    limite = quota(utilisateur)
    if limite is None:
        return None
    return max(0, limite - consommes(utilisateur))


def peut_rendre(utilisateur: Utilisateur) -> bool:
    reste = restants(utilisateur)
    return reste is None or reste > 0


def noter(utilisateur: Utilisateur, type_: str, detail: str = "") -> None:
    users.noter(utilisateur.id, type_, detail)


def changer_de_formule(utilisateur: Utilisateur, plan_id: str) -> Utilisateur:
    if plan_id in {plan.id for plan in plans.PLANS}:
        return users.mettre_a_jour(utilisateur.id, plan=plan_id) or utilisateur
    return utilisateur


def resume(utilisateur: Utilisateur) -> dict:
    """Ce que les gabarits affichent : formule, crédits, droits."""
    plan = formule(utilisateur)
    return {
        "plan": {
            "id": plan.id, "name": plan.name, "videos": plan.videos,
            "price_monthly": plan.price_monthly, "pro": est_pro(utilisateur),
        },
        "credits": {
            "consommes": consommes(utilisateur),
            "restants": restants(utilisateur),
            "quota": quota(utilisateur),
        },
    }
