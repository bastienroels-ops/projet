# Mettre Flambée en ligne

Tout est prêt pour un déploiement en conteneur : `Dockerfile`, `docker-compose.yml`
et un `Caddyfile` qui obtient le certificat HTTPS tout seul.

## Gratuit, depuis un iPhone : Oracle Cloud

Oracle offre, sans limite de durée, un serveur ARM de 4 cœurs et 24 Go de
mémoire — plus puissant que la plupart des offres payantes d'entrée de gamme.
Toute la chaîne de Flambée existe en ARM64, transcription comprise : les roues
`ctranslate2`, `onnxruntime`, `av` et `tokenizers` ont toutes une version
`aarch64`, ce qui a été vérifié avant d'écrire ce chapitre.

L'installation ne demande aucun terminal : le fichier
[`deploiement/oracle-cloud-init.yaml`](../deploiement/oracle-cloud-init.yaml)
se colle dans le formulaire de création du serveur, et la machine fait le reste
— Docker, construction de l'image, pare-feu, certificat HTTPS, sauvegarde
nocturne.

### La marche à suivre

1. **Une adresse gratuite.** Sur [duckdns.org](https://www.duckdns.org),
   connecte-toi (GitHub ou Google), choisis un nom — `flambee-bastien` par
   exemple — et note le jeton affiché en haut de la page.
2. **Le serveur.** Crée une instance *Ampere A1* (ARM) sous Ubuntu 22.04 ou
   24.04, avec 2 à 4 cœurs et 6 à 24 Go.
3. **Le fichier.** Ouvre `deploiement/oracle-cloud-init.yaml`, renseigne les
   deux champs DuckDNS en haut, puis colle tout le contenu dans
   *Show advanced options* → *Paste cloud-init script*.
4. **Attendre.** Compter une dizaine de minutes : la construction de l'image
   est le poste le plus long. Le site répond ensuite sur
   `https://<ton-nom>.duckdns.org`.

Le premier compte créé est le tien, et il reçoit d'office la formule Studio :
crédits illimités, toutes les rubriques. Ferme les inscriptions juste après
(`FLAMBEE_SIGNUP=ferme` dans `/opt/flambee/.env`).

### Pourquoi DuckDNS et pas sslip.io

`sslip.io` ne demande aucune inscription et transforme une IP en nom de
domaine, ce qui est séduisant. Mais il ne figure pas sur la *Public Suffix
List* : tous ses sous-domaines partagent donc le même quota de certificats
Let's Encrypt, et l'obtention du HTTPS échoue de façon imprévisible.
`duckdns.org` y figure, et chaque sous-domaine dispose de son propre quota.
Le fichier accepte les deux, mais se rabat sur `sslip.io` uniquement si rien
n'est renseigné, en le signalant dans le journal.

### Ce que le fichier fait, et ses limites

Il ouvre les ports 80 et 443 dans le pare-feu local — c'est la cause numéro un
d'un site injoignable alors que le conteneur tourne, les images Oracle rejetant
tout sauf SSH. Il engendre la clé de signature des sessions sur la machine,
pour qu'elle ne transite par aucun formulaire. Il installe une sauvegarde
quotidienne du volume dans `/var/backups/flambee`, et garde une semaine.

Reste à faire de ton côté : ouvrir aussi les ports 80 et 443 dans la *Security
List* du réseau virtuel, côté console Oracle — le pare-feu de la machine ne
suffit pas, celui du réseau compte aussi.

Ce fichier a été relu et ses chemins testés un par un (adresse DuckDNS valide,
jeton refusé, domaine propre, repli), mais **il n'a pas été exécuté sur une
vraie instance Oracle** : je n'y ai pas accès. En cas d'échec, le journal se
lit dans `/var/log/flambee-installation.log`.

### Si le serveur gratuit n'est pas disponible

Les instances ARM gratuites sont souvent en rupture dans les régions
populaires. Deux replis : essayer une autre région, ou rester sur Google Colab
(voir le README) — gratuit aussi, mais l'adresse change à chaque lancement et
la machine est reprise au bout de quelques heures.

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
| `FLAMBEE_SMTP_*` | Facultatif, mais recommandé dès qu'on ouvre les inscriptions : sans serveur d'envoi, un utilisateur qui oublie son mot de passe dépend de toi pour retrouver son lien dans `docker compose logs flambee`. Renseigne `HOTE`, `PORT`, `UTILISATEUR`, `MOT_DE_PASSE` et au besoin `EXPEDITEUR`. |

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
