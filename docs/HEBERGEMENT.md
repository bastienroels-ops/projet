# Mettre Flambée en ligne

Tout est prêt pour un déploiement en conteneur : `Dockerfile`, `docker-compose.yml`
et un `Caddyfile` qui obtient le certificat HTTPS tout seul.

## Ce qui a été vérifié

L'image a été construite et exécutée, et le parcours complet a tourné dedans :
inscription, ouverture de session, import de deux vidéos, import d'une voix
avec transcription locale, puis rendu d'une vidéo de 29 secondes en 1080 × 1920
— seize secondes d'encodage, un crédit décompté. Les polices embarquées sont
bien trouvées par ffmpeg dans le conteneur.

## Ce qu'il faut

- Un serveur Linux avec Docker. Deux cœurs et 4 Go de mémoire suffisent pour
  démarrer ; l'encodage est le poste qui décide, pas le web.
- Un nom de domaine pointant vers l'adresse IP du serveur (enregistrement A).
- Les ports 80 et 443 ouverts.

## Mise en route

```bash
git clone <ton-dépôt> flambee && cd flambee
cp .env.exemple .env

# Engendre la clé de signature des sessions, une fois pour toutes.
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# …et colle-la dans .env, avec ton nom de domaine.

docker compose up -d
```

Caddy demande le certificat au premier démarrage : compte une minute avant que
`https://ton-domaine` réponde. `docker compose logs -f` montre l'avancement.

Le premier compte créé est le tien. Ferme ensuite les inscriptions, ou exige un
code :

```bash
FLAMBEE_SIGNUP=ferme          # plus personne ne s'inscrit
FLAMBEE_INVITE_CODE=un-code   # ou : seulement avec ce code
```

## Ce qui persiste

Un seul volume, `donnees`, contient tout l'état : la base des comptes
(`flambee.db`), les projets, les voix importées et les vidéos rendues. L'image,
elle, est jetable.

```bash
# Sauvegarde
docker run --rm -v flambee_donnees:/d -v "$PWD":/sortie alpine \
  tar czf /sortie/flambee-sauvegarde.tar.gz -C /d .
```

Sauvegarde la base **avant** toute mise à jour. Une perte de `flambee.db`, ce
sont tous les comptes perdus.

## Réglages qui comptent

| Variable | Pourquoi |
|---|---|
| `FLAMBEE_SECRET_KEY` | Signe les sessions. La changer déconnecte tout le monde ; la laisser vide en fait engendrer une, mais sur un volume neuf elle changerait à chaque recréation. |
| `FLAMBEE_NICE` | Au-dessus de 0, l'encodage laisse respirer le réseau. Sur une petite machine, `5` à `10` évite que le site devienne injoignable pendant un rendu. |
| `FLAMBEE_WHISPER_MODEL` | `base` par défaut. `small` transcrit mieux mais demande environ trois fois plus de temps processeur. |
| `FLAMBEE_MAX_UPLOAD_MB` | À accorder avec `request_body max_size` dans le `Caddyfile`. |

## Monter en charge

L'application est mono-processus par choix : un rendu occupe un cœur pendant
des dizaines de secondes, et plusieurs rendus simultanés se gêneraient de toute
façon. Tant que tu es seul ou à quelques-uns, cela suffit.

Au-delà, l'ordre des travaux est : sortir l'encodage dans une file d'attente
(Redis et des workers séparés), puis déplacer les fichiers vers un stockage
objet. `docs/COMMERCIALISATION.md` détaille ce chemin.

## Avant d'ouvrir au public

1. **Complète les mentions légales.** Les zones `[à compléter]` de
   `flambee/site.py` demandent une identité d'éditeur et un hébergeur. Publier
   sans elles est une infraction en France.
2. **Tranche la question des sources.** Le téléchargement depuis TikTok et
   YouTube contrevient aux conditions de ces plateformes : toléré en usage
   privé, c'est un risque réel dans un service payant. L'import de fichiers
   reste la voie sûre.
3. **Sauvegarde automatique.** Une tâche `cron` qui archive le volume chaque
   nuit coûte cinq minutes à écrire et évite une catastrophe.
4. **Surveille l'espace disque.** Les vidéos rendues s'accumulent. Prévois leur
   effacement au bout de quelques semaines.
