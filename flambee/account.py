"""Compte, formule et crédits.

Un seul compte pour l'instant — l'outil reste mono-utilisateur. Mais la
comptabilité des crédits est réelle : chaque rendu est décompté, les quotas de
la formule sont appliqués, et l'historique est conservé. Le jour où les comptes
multiples arrivent, c'est ce module qui devient une table.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, plans

_LOCK = threading.RLock()

# Combien de rendus par mois selon la formule. None = sans limite.
QUOTAS: dict[str, int | None] = {
    "essai": 3,
    "createur": 30,
    "studio": None,
}

# Les formules qui ouvrent Script Viral et Voice Studio.
FORMULES_PRO = {"createur", "studio"}


@dataclass
class Evenement:
    date: str
    type: str            # "rendu" | "apercu" | "transcription"
    detail: str = ""


@dataclass
class Compte:
    plan: str = "essai"
    nom: str = ""
    email: str = ""
    cree_le: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))
    historique: list[dict] = field(default_factory=list)

    # --- Formule ---------------------------------------------------------
    @property
    def formule(self) -> plans.Plan:
        for plan in plans.PLANS:
            if plan.id == self.plan:
                return plan
        return plans.PLANS[0]

    @property
    def pro(self) -> bool:
        return self.plan in FORMULES_PRO

    @property
    def quota(self) -> int | None:
        return QUOTAS.get(self.plan, 3)

    # --- Crédits ---------------------------------------------------------
    def periode(self) -> str:
        return time.strftime("%Y-%m")

    def consommes(self, periode: str | None = None) -> int:
        periode = periode or self.periode()
        return sum(1 for e in self.historique
                   if e.get("type") == "rendu" and e.get("date", "").startswith(periode))

    def restants(self) -> int | None:
        if self.quota is None:
            return None
        return max(0, self.quota - self.consommes())

    def peut_rendre(self) -> bool:
        restants = self.restants()
        return restants is None or restants > 0

    @property
    def initiales(self) -> str:
        source = (self.nom or self.email or "Flambée").strip()
        morceaux = [m for m in source.replace("@", " ").split() if m]
        if not morceaux:
            return "FL"
        if len(morceaux) == 1:
            return morceaux[0][:2].upper()
        return (morceaux[0][0] + morceaux[1][0]).upper()

    # --- Persistance -----------------------------------------------------
    def to_dict(self) -> dict:
        données = asdict(self)
        données["restants"] = self.restants()
        données["consommes"] = self.consommes()
        données["quota"] = self.quota
        données["pro"] = self.pro
        données["initiales"] = self.initiales
        return données


def _chemin() -> Path:
    return config.WORK_DIR / "compte.json"


def charger() -> Compte:
    chemin = _chemin()
    if not chemin.exists():
        return Compte()
    try:
        données = json.loads(chemin.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Compte()
    connus = {"plan", "nom", "email", "cree_le", "historique"}
    return Compte(**{k: v for k, v in données.items() if k in connus})


def enregistrer(compte: Compte) -> None:
    with _LOCK:
        chemin = _chemin()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_suffix(".tmp")
        temporaire.write_text(
            json.dumps({
                "plan": compte.plan, "nom": compte.nom, "email": compte.email,
                "cree_le": compte.cree_le, "historique": compte.historique[-500:],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporaire.replace(chemin)


def noter(type_: str, detail: str = "") -> Compte:
    """Ajoute un évènement à l'historique et retourne le compte à jour."""
    with _LOCK:
        compte = charger()
        compte.historique.append({
            "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": type_,
            "detail": detail[:160],
        })
        enregistrer(compte)
        return compte


def changer_de_formule(plan_id: str) -> Compte:
    with _LOCK:
        compte = charger()
        if plan_id in {p.id for p in plans.PLANS}:
            compte.plan = plan_id
            enregistrer(compte)
        return compte


def mettre_a_jour_profil(nom: str = "", email: str = "") -> Compte:
    with _LOCK:
        compte = charger()
        compte.nom = nom.strip()[:80]
        compte.email = email.strip()[:254]
        enregistrer(compte)
        return compte
