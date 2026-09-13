"""Envoi d'e-mails, quand un serveur SMTP est configuré.

Sans configuration, rien n'est envoyé et l'appelant en est informé : mieux vaut
dire à l'utilisateur d'écrire au support que lui laisser croire qu'un message
est parti.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


def configure() -> bool:
    return bool(os.environ.get("FLAMBEE_SMTP_HOTE", "").strip())


def _reglages() -> dict:
    return {
        "hote": os.environ.get("FLAMBEE_SMTP_HOTE", "").strip(),
        "port": int(os.environ.get("FLAMBEE_SMTP_PORT", "587")),
        "utilisateur": os.environ.get("FLAMBEE_SMTP_UTILISATEUR", "").strip(),
        "mot_de_passe": os.environ.get("FLAMBEE_SMTP_MOT_DE_PASSE", ""),
        "expediteur": os.environ.get("FLAMBEE_SMTP_EXPEDITEUR", "").strip()
                      or os.environ.get("FLAMBEE_SMTP_UTILISATEUR", "").strip(),
    }


def envoyer(destinataire: str, sujet: str, corps: str) -> bool:
    """Envoie un message. Retourne False si l'envoi n'a pas pu se faire."""
    if not configure():
        log.info("Aucun SMTP configuré : message pour %s non envoyé.", destinataire)
        return False

    reglages = _reglages()
    message = EmailMessage()
    message["From"] = reglages["expediteur"]
    message["To"] = destinataire
    message["Subject"] = sujet
    message.set_content(corps)

    try:
        contexte = ssl.create_default_context()
        if reglages["port"] == 465:
            serveur = smtplib.SMTP_SSL(reglages["hote"], reglages["port"],
                                       context=contexte, timeout=20)
        else:
            serveur = smtplib.SMTP(reglages["hote"], reglages["port"], timeout=20)
            serveur.starttls(context=contexte)
        with serveur:
            if reglages["utilisateur"]:
                serveur.login(reglages["utilisateur"], reglages["mot_de_passe"])
            serveur.send_message(message)
    except Exception as exc:                      # réseau, authentification…
        log.error("Envoi impossible vers %s : %s", destinataire, exc)
        return False

    log.info("Message envoyé à %s.", destinataire)
    return True
