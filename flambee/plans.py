"""Formules d'abonnement et contenu du site public.

Tout est défini ici plutôt que dans les gabarits : changer un prix, une limite
ou un argument se fait à un seul endroit, sans toucher au HTML.

⚠️ Les tarifs ci-dessous sont une proposition de départ, à ajuster. Aucun
paiement n'est encore branché : voir `docs/COMMERCIALISATION.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    tagline: str
    price_monthly: int          # en euros, 0 = gratuit
    price_yearly: int           # par mois, facturé à l'année
    videos: str
    features: list[str]
    cta: str = "Commencer"
    highlight: bool = False     # la formule mise en avant
    note: str = ""


# Chaque ligne ci-dessous correspond à une règle appliquée dans le code —
# `account.py` pour les droits, `pipeline.py` pour le filigrane, `project.py`
# pour la conservation. Rien n'y figure qui ne soit vérifié par un test : une
# page de tarifs qui promet ce que le produit ne fait pas est un mensonge
# vendu, et le premier client s'en aperçoit.
PLANS: list[Plan] = [
    Plan(
        id="essai",
        name="Essai",
        tagline="Pour voir ce que ça donne sur tes propres vidéos.",
        price_monthly=0,
        price_yearly=0,
        videos="3 vidéos par mois",
        features=[
            "Montage automatique en 1080 × 1920",
            "Deux voix de synthèse françaises",
            "Deux styles de sous-titres",
            "Aperçus illimités, jamais décomptés",
        ],
        cta="Créer ma première vidéo",
        note="Les rendus portent une mention « Flambée ». "
             "Projets conservés 7 jours.",
    ),
    Plan(
        id="createur",
        name="Créateur",
        tagline="Le rythme de publication quotidien, sans y passer les soirées.",
        price_monthly=19,
        price_yearly=15,
        videos="30 vidéos par mois",
        features=[
            "Rendus sans filigrane",
            "Les six styles de sous-titres",
            "Les dix voix françaises, débit réglable",
            "Script Viral : le texte minuté d'une vidéo",
            "Voice Studio : ta voix, sous-titres calés dessus",
            "Musique de fond et mixage automatique",
            "Projets conservés 90 jours",
        ],
        cta="Passer au rythme quotidien",
        highlight=True,
        note="Le choix de la plupart des créateurs réguliers.",
    ),
    Plan(
        id="studio",
        name="Studio",
        tagline="Pour ceux qui publient sans compter.",
        price_monthly=49,
        price_yearly=39,
        videos="Vidéos illimitées",
        features=[
            "Tout le Créateur, sans plafond mensuel",
            "Projets conservés sans limite de durée",
        ],
        cta="Parler à quelqu'un",
        note="Profils de marque, styles sur mesure, export en lot et accès "
             "à l'API sont en cours de développement, et ne sont pas encore "
             "compris.",
    ),
]


@dataclass(frozen=True)
class Feature:
    title: str
    body: str
    icon: str          # nom d'une icône de `icones.py`, pas un emoji


FEATURES: list[Feature] = [
    Feature("Les coupes tombent juste", "Les changements de plan de chaque source "
            "sont détectés, et le montage y cale ses coupes. Rien n'est tranché au "
            "milieu d'un geste.", "ciseaux"),
    Feature("L'accroche est notée", "Les trois premières secondes de chaque source "
            "sont mesurées — mouvement, énergie sonore, popularité — et la plus "
            "percutante t'est proposée en tête.", "eclair"),
    Feature("Des sous-titres au mot près", "Le minutage vient de la synthèse vocale "
            "elle-même : chaque mot s'allume quand il est prononcé. Six styles, du "
            "plus viral au plus sobre.", "sous-titres"),
    Feature("Un son de vraie production", "Voix normalisée aux standards de "
            "diffusion, musique qui s'efface d'elle-même sous la parole, limiteur "
            "en sortie.", "curseurs"),
    Feature("Un seul encodage", "Découpe, recadrage, montage, sous-titres et mixage "
            "tiennent dans une seule passe : c'est plus rapide, et l'image ne "
            "subit aucune perte de génération.", "engrenage"),
    Feature("Vertical par construction", "Tout est pensé pour le 9:16 : recadrage "
            "centré, masquage des sous-titres d'origine, léger travelling sur les "
            "plans fixes.", "telephone"),
]


@dataclass(frozen=True)
class Step:
    number: str
    title: str
    body: str


STEPS: list[Step] = [
    Step("01", "Tu donnes la matière", "Deux à cinq liens sur une même thématique, "
         "ou des vidéos déjà sur ton téléphone."),
    Step("02", "Tu choisis l'accroche", "Les premières secondes de chaque source, "
         "côte à côte, classées par impact."),
    Step("03", "Tu poses le style", "Voix, sous-titres, musique, travelling. "
         "Un aperçu montre le rendu exact."),
    Step("04", "Tu valides le script", "Sujet et consignes suffisent. Le texte "
         "reste éditable jusqu'au bout."),
    Step("05", "Tu récupères le fichier", "Un .mp4 vertical, sous-titré, mixé, "
         "prêt à publier."),
]


@dataclass(frozen=True)
class Question:
    q: str
    a: str


FAQ: list[Question] = [
    Question("Faut-il installer quelque chose ?",
             "Non. Flambée tourne dans le navigateur, y compris sur téléphone. "
             "Le montage s'effectue côté serveur."),
    Question("D'où viennent les vidéos sources ?",
             "De liens que tu colles, ou de fichiers déjà enregistrés sur ton "
             "appareil. Tu restes responsable des droits sur ce que tu importes : "
             "réutiliser la vidéo d'un tiers sans son accord n'est pas permis, et "
             "Flambée ne te couvre pas là-dessus."),
    Question("La voix est-elle vraiment naturelle ?",
             "Ce sont des voix de synthèse neuronales françaises, avec plusieurs "
             "timbres et débits. Elles conviennent au format court ; elles ne "
             "remplacent pas un comédien."),
    Question("Puis-je modifier le script avant le rendu ?",
             "Oui, à tout moment. Le texte est éditable, et le compteur t'indique "
             "la durée de voix off correspondante."),
    Question("Combien de temps prend un rendu ?",
             "Quelques dizaines de secondes pour une vidéo d'une minute. L'aperçu "
             "en 540p, lui, arrive en quelques secondes."),
    Question("Puis-je annuler mon abonnement ?",
             "Oui, à tout moment et sans justification. L'accès reste ouvert "
             "jusqu'à la fin de la période déjà réglée."),
]
