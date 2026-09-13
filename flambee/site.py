"""Le site public : les textes du cadre légal.

Les textes juridiques sont volontairement lacunaires là où seule une personne
peut renseigner : identité de l'éditeur, statut, hébergeur. Publier un service
en France sans ces mentions est une infraction — mieux vaut un texte qui montre
ce qui manque qu'un texte plausible mais faux.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bloc:
    titre: str
    paragraphes: list[str]


A_COMPLETER = "[à compléter]"

MENTIONS = [
    Bloc("Éditeur du service", [
        f"Le service Flambée est édité par {A_COMPLETER} (nom ou raison "
        f"sociale), {A_COMPLETER} (statut juridique et capital le cas échéant), "
        f"dont le siège est situé {A_COMPLETER}.",
        f"Numéro d'immatriculation : {A_COMPLETER}. "
        f"Numéro de TVA intracommunautaire : {A_COMPLETER}.",
        f"Directeur de la publication : {A_COMPLETER}. "
        f"Contact : {A_COMPLETER}.",
    ]),
    Bloc("Hébergement", [
        f"Le service est hébergé par {A_COMPLETER}, dont le siège est situé "
        f"{A_COMPLETER}. Contact : {A_COMPLETER}.",
    ]),
    Bloc("Propriété intellectuelle", [
        "La marque, l'interface et le code de Flambée sont la propriété de son "
        "éditeur. Les vidéos produites appartiennent à l'utilisateur, dans la "
        "limite des droits qu'il détient sur les contenus qu'il a fournis.",
        "Flambée utilise des logiciels libres — notamment ffmpeg (LGPL/GPL) et "
        "yt-dlp (Unlicense) — ainsi que des polices publiées sous SIL Open Font "
        "License. Ces licences sont respectées et consultables dans le dépôt du "
        "projet.",
    ]),
]

CONDITIONS = [
    Bloc("Objet", [
        "Flambée est un outil de montage automatisé : à partir de vidéos "
        "fournies par l'utilisateur, il produit une vidéo verticale sous-titrée "
        "et sonorisée.",
    ]),
    Bloc("Responsabilité sur les contenus fournis", [
        "L'utilisateur garantit détenir les droits sur les vidéos qu'il importe "
        "ou dont il fournit le lien. Récupérer la vidéo d'un tiers sans son "
        "accord, ou contourner les conditions d'utilisation d'une plateforme, "
        "engage l'utilisateur seul.",
        "L'éditeur ne contrôle pas les contenus traités et se réserve le droit "
        "de suspendre un compte en cas d'usage manifestement illicite.",
    ]),
    Bloc("Abonnement et résiliation", [
        f"Les formules et leurs tarifs sont présentés sur la page Tarifs. "
        f"Les modalités de facturation, de renouvellement et de remboursement "
        f"restent à préciser : {A_COMPLETER}.",
        "L'abonnement est résiliable à tout moment. L'accès demeure ouvert "
        "jusqu'au terme de la période déjà réglée.",
        "Conformément au droit de la consommation, le consommateur dispose d'un "
        "délai de rétractation de quatorze jours, sauf renoncement exprès de sa "
        "part pour une exécution immédiate du service.",
    ]),
    Bloc("Disponibilité", [
        "Le service est fourni en l'état, sans garantie de disponibilité "
        "ininterrompue. Une interruption pour maintenance peut survenir.",
    ]),
]

CONFIDENTIALITE = [
    Bloc("Données collectées", [
        "Adresse e-mail, lorsqu'elle est communiquée volontairement pour être "
        "prévenu de l'ouverture des comptes, accompagnée de la formule "
        "d'intérêt et, facultativement, du type de contenus publiés.",
        "Fichiers vidéo importés ou téléchargés, et fichiers produits. Ils sont "
        "conservés le temps du traitement et de la récupération du résultat.",
    ]),
    Bloc("Finalités et base légale", [
        "L'adresse e-mail sert uniquement à annoncer l'ouverture du service : "
        "base légale, le consentement, retirable à tout moment.",
        "Les fichiers vidéo sont traités pour exécuter le service demandé : "
        "base légale, l'exécution du contrat.",
    ]),
    Bloc("Durée de conservation", [
        f"Adresses e-mail : jusqu'au retrait du consentement, et au plus tard "
        f"{A_COMPLETER}.",
        "Fichiers vidéo : supprimés à la fin du traitement ou à l'expiration de "
        "la session de travail.",
    ]),
    Bloc("Tes droits", [
        "Tu disposes d'un droit d'accès, de rectification, d'effacement, de "
        "limitation et de portabilité, ainsi que du droit d'introduire une "
        f"réclamation auprès de la CNIL. Pour les exercer : {A_COMPLETER}.",
    ]),
    Bloc("Sous-traitants et transferts", [
        "La synthèse vocale s'appuie sur un service tiers (Microsoft Edge TTS) : "
        "le texte du script lui est transmis pour produire l'audio.",
        f"La génération de script, lorsqu'elle est activée, transmet le sujet "
        f"saisi à l'API d'Anthropic. Hébergement et éventuels transferts hors "
        f"Union européenne : {A_COMPLETER}.",
    ]),
    Bloc("Traceurs", [
        "Le site ne dépose aucun cookie publicitaire ni de mesure d'audience. "
        "Le navigateur conserve localement l'identifiant du projet en cours, "
        "pour te le retrouver au retour.",
    ]),
]

PAGES_LEGALES = {
    "mentions-legales": ("Informations légales", "Mentions légales", MENTIONS),
    "conditions": ("Le cadre", "Conditions d'utilisation", CONDITIONS),
    "confidentialite": ("Tes données", "Politique de confidentialité", CONFIDENTIALITE),
}
