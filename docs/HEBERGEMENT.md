# Mettre Flambée en ligne

Tout est prêt pour un déploiement en conteneur : `Dockerfile`, `docker-compose.yml`
et un `Caddyfile` qui obtient le certificat HTTPS tout seul.

## Le moins cher de tout : publier le site public en pages statiques

Il faut distinguer deux choses, parce qu'elles n'ont pas du tout le même coût.

| | Ce que c'est | Ce qu'il faut |
|---|---|---|
| **Le site public** | Accueil, fonctionnalités, tarifs, questions, pages légales | Rien. Du HTML et des fichiers. |
| **L'atelier** | Créer un compte, monter une vidéo, transcrire | Python, ffmpeg, un disque — un serveur. |

Le site public ne calcule rien : une fois les clips de démonstration rendus,
il ne reste que des pages. Un hébergeur de pages statiques suffit, et ils sont
gratuits — pour de bon, sans carte bancaire ni période d'essai.

### Fabriquer la version statique

```bash
python outils/exporter_site.py --sortie export
```

Compte une minute : le script rend les six échantillons de style et la
démonstration de l'accroche, copie les ressources, fige les sept pages, et
produit une page 404. Le tout tient dans **environ un demi-mégaoctet**.

Quand l'atelier tournera quelque part, donne son adresse pour que les boutons
« Créer un compte » et « Connexion » y mènent :

```bash
python outils/exporter_site.py --sortie export \
    --atelier https://flambee-bastien.duckdns.org
```

Sans cette option, le script prévient : ces boutons pointeraient vers des
pages absentes du site statique.

### Publier sur Cloudflare Pages, depuis un téléphone

Le dossier `export/` est **versionné dans le dépôt**, déjà construit. C'est
délibéré : l'environnement de construction de Cloudflare n'a pas ffmpeg, et
une commande de build qui doit rendre les clips y échouerait. En publiant le
résultat, il ne reste plus rien qui puisse casser — Cloudflare sert des
fichiers, c'est tout.

1. Ouvrir [dash.cloudflare.com](https://dash.cloudflare.com) et créer un
   compte (gratuit, aucune carte bancaire).
2. **Workers & Pages** → **Create** → onglet **Pages** → **Connect to Git**.
3. Autoriser GitHub, choisir le dépôt `bastienroels-ops/projet`.
4. Dans les réglages de construction :

   | Champ | Valeur |
   |---|---|
   | Framework preset | **None** |
   | Build command | *(laisser vide)* |
   | Build output directory | `export` |
   | Branch | `claude/fastapi-viral-video-montage-e9x7i8` |

5. **Save and Deploy**. Une minute plus tard, le site répond sur une adresse
   en `.pages.dev`.

Chaque envoi sur la branche republie le site tout seul.

### Mettre le site à jour

Le dossier publié est le résultat d'une construction : après toute
modification du site, il faut le refaire et le committer.

```bash
python outils/exporter_site.py --sortie export
git add export && git commit -m "Republier le site" && git push
```

### Un nom de domaine

L'adresse `.pages.dev` est gratuite et définitive. Pour un nom à toi :
**Custom domains** dans le projet Pages, puis suivre les instructions. Le
domaine coûte une dizaine d'euros par an chez n'importe quel registrar — et
rien de plus : Cloudflare ne facture ni le HTTPS, ni le trafic, ni les
certificats.

### Les autres hébergeurs gratuits

| Hébergeur | Coût | Remarque |
|---|---|---|
| **GitHub Pages** | 0 € | Ton dépôt y est déjà. Publié sous `nom.github.io/projet`, il faut alors exporter avec `--prefixe /projet`. |
| **Netlify** | 0 € | 100 Go de trafic par mois, largement au-dessus du besoin. |

### Fermer le site derrière un code

```bash
python outils/exporter_site.py --sortie export --code "lune-violette"
```

Le site demande alors un code avant de montrer quoi que ce soit. Le réglage
est conservé dans `deploiement/porte.json` : les exports suivants gardent la
porte sans qu'on ait à y penser, et un test le vérifie. Pour changer le code,
relance la commande avec un autre ; pour rouvrir le site à tout le monde,
`--sans-code`.

Le code n'est écrit nulle part — ni dans ce fichier, ni dans les pages. Ce qui
l'est, c'est son empreinte : deux cent mille tours de PBKDF2 sur un sel tiré au
hasard à la publication.

**Ce que cette porte vaut, exactement.** Un site de pages statiques n'a pas de
serveur : personne, à l'autre bout, ne peut vérifier un mot de passe. La porte
est donc dessinée par le navigateur du visiteur, et le contenu des pages lui
est livré en même temps qu'elle. Elle le masque ; elle ne l'enferme pas.

- Elle tient le site à l'écart de qui tombe dessus par hasard, et des moteurs
  de recherche. C'est ce pour quoi elle est faite, et elle le fait bien.
- Elle ne résiste pas à quelqu'un qui sait ouvrir les outils de développement
  de son navigateur, ni à une machine qui essaie les codes un par un.

Autrement dit : bonne pour un lien qu'on donne à des amis, insuffisante pour
quoi que ce soit de confidentiel. Pour une vraie serrure, il faut un hébergeur
qui vérifie avant de servir — **Cloudflare Access** le fait gratuitement
jusqu'à cinquante personnes, et se règle depuis le tableau de bord Cloudflare,
sans toucher au code.

### Ce que la version statique ne fait pas

L'essayage libre des sous-titres demande ffmpeg à chaque phrase : impossible
sans serveur. Le bloc reste vivant — les six écritures défilent sur les clips
déjà calculés — mais le champ de saisie disparaît. Il retrouve toute sa
fonction dès que le site est servi par l'application.

## Ne plus jamais voir « Error 530 » ni « Error 1033 »

Ces deux codes ne viennent pas de Flambée. Ils viennent de Cloudflare, et ils
disent la même chose de deux façons :

| Code | Ce que Cloudflare raconte | Ce qui s'est passé |
|---|---|---|
| **530** | « Je n'arrive pas à joindre l'origine » | Le tunnel existe, mais sa liaison avec le Colab est rompue — souvent parce qu'un encodage sature la machine. Ça revient presque toujours tout seul. |
| **1033** | « Je ne sais pas résoudre ce nom » | Le tunnel n'existe plus du tout. Adresse de la veille ouverte depuis un signet, session Colab reprise par Google, ou tunnel rouvert entre-temps à une autre adresse. |

Le carnet Colab absorbe les deux autant qu'il est possible : il donne une
seconde adresse qui ne traverse aucun tunnel, l'application propose un bouton
vers cette adresse dès qu'un appel échoue, et la surveillance rouvre un tunnel
mort au bout d'une minute. Mais un tunnel gratuit reste un tunnel gratuit, et
une machine Colab reste une machine que Google reprend.

**La seule façon de ne plus jamais les voir est de ne plus avoir de tunnel** :
un serveur avec sa propre adresse, allumé en permanence. C'est exactement ce
que décrit le chapitre suivant, et il est gratuit.

## Quitter Colab : un serveur à soi

Un serveur permanent supprime l'erreur 1033 à la racine — il n'y a plus de
tunnel — et avec elle tout le reste : l'adresse qui change, la machine reprise
au bout de quelques heures, les vidéos à télécharger avant la fin de la
session, le rendu qui s'arrête quand on ferme l'onglet.

L'installation ne demande **aucun terminal** : le fichier
[`deploiement/serveur-cloud-init.yaml`](../deploiement/serveur-cloud-init.yaml)
se colle dans le formulaire de création du serveur, et la machine fait le
reste — Docker, construction de l'image, pare-feu, certificat HTTPS,
sauvegarde nocturne, mises à jour.

### Quel hébergeur

Le même fichier s'utilise tel quel partout où il existe un champ *cloud-init*.
Le choix ne change que le formulaire où on le colle.

| | Prix | Ce qu'on y gagne | Ce qu'on y perd |
|---|---|---|---|
| **Hetzner** | ~4 €/mois | S'ouvre en cinq minutes, sans carte à faire valider, et la machine existe vraiment | Quatre euros par mois |
| **Oracle Cloud « Always Free »** | 0 € | 4 cœurs ARM et 24 Go | L'inscription demande une carte bancaire (non débitée), les instances ARM gratuites sont souvent en rupture, et le compte peut être suspendu sans préavis |

Si Flambée devient ton outil de travail, les quatre euros achètent surtout de
ne plus jamais y penser.

### Chez Hetzner, depuis Safari

Compter vingt minutes en tout, dont dix d'attente pendant lesquelles il n'y a
rien à faire.

**1. L'adresse (2 min).** Sur [duckdns.org](https://www.duckdns.org),
connecte-toi (GitHub ou Google), tape un nom dans le champ *sub domain* —
`flambee-bastien` par exemple — et touche **add domain**. Note le **token**
affiché en haut de la page : une longue suite de lettres et de chiffres.

> Pourquoi pas directement l'adresse IP du serveur : Let's Encrypt ne délivre
> de certificat HTTPS que pour un nom, jamais pour une IP. Et `duckdns.org`
> figure sur la *Public Suffix List*, ce qui donne à chaque sous-domaine son
> propre quota de certificats — `sslip.io`, qui ne demande aucune
> inscription, partage un quota unique entre tous ses utilisateurs, et le
> HTTPS y échoue de façon imprévisible.

**2. Le compte Hetzner (5 min).** [console.hetzner.cloud](https://console.hetzner.cloud)
→ *Sign up*. Vérification par courriel, puis une carte ou PayPal. Crée un
projet, appelle-le *Flambée*.

**3. Le fichier à coller (3 min).** Ouvre
[`deploiement/serveur-cloud-init.yaml`](../deploiement/serveur-cloud-init.yaml)
sur GitHub, touche l'icône *Copy raw file*. Colle-le dans l'app **Notes**, et
remplis les deux lignes du haut :

```
DUCKDNS_SOUS_DOMAINE="flambee-bastien"
DUCKDNS_JETON="le-token-copié-à-l-étape-1"
```

Si tu as une clé API Anthropic, mets-la dans `ANTHROPIC_API_KEY` au passage :
le bouton *Écrire le script avec Claude* fonctionnera alors en un clic.
Laisse tout le reste tel quel. Sélectionne tout, copie.

**4. La machine (3 min).** Dans le projet Hetzner : **Add Server**.

| Champ | Valeur | Pourquoi |
|---|---|---|
| Location | **Falkenstein** ou **Nuremberg** | Allemagne : le plus proche de la France, donc le plus rapide |
| Image | **Ubuntu 24.04** | Le fichier attend Ubuntu et refuse de s'installer ailleurs, en le disant |
| Type | onglet **Arm64** → **CAX11** | 2 cœurs, 4 Go, 40 Go de disque, ~3,90 €/mois |
| SSH keys | *laisser vide* | Tu n'ouvriras jamais de terminal. Hetzner enverra un mot de passe root par courriel : garde-le, sans t'en servir |
| **Cloud config** | **colle ici le fichier de l'étape 3** | C'est tout le contenu de l'installation |
| Name | `flambee` | |

Puis **Create & Buy now**.

> **CAX11 ou CAX21 ?** CAX11 (2 cœurs, 4 Go, 40 Go) suffit et encode à peu
> près comme Colab. CAX21 (4 cœurs, 8 Go, 80 Go, ~6,50 €/mois) divise le temps
> de rendu par deux environ et laisse de la place aux vidéos. Si tu montes
> plusieurs vidéos par jour, prends le CAX21 — on peut changer de taille plus
> tard, mais pas réduire le disque.

**5. Regarder (10 min).** Une minute après la création, ouvre dans Safari :

```
http://flambee-bastien.duckdns.org
```

en **http**, sans le `s` — le certificat n'existe pas encore. Une page
affiche l'étape en cours et se rafraîchit toute seule :

> **Flambée s'installe**
> *Rien à faire de ton côté.*
> **Étape 6 sur 7** — Construction de l'image (plusieurs minutes)

Elle existe pour une raison précise : sans elle, la machine ne répond rien
pendant dix minutes, ce qui est exactement ce qu'elle ferait si l'installation
avait échoué. On ne pouvait pas distinguer les deux, et il n'y a pas de
terminal sur un iPhone pour aller voir le journal. Si quelque chose s'arrête,
cette même page le dit en français et montre les dernières lignes du journal.
Tu peux la fermer : l'installation continue sans elle.

**6. Entrer.** Quand la page affiche **Ouvrir Flambée**, touche le bouton. Tu
arrives sur `https://flambee-bastien.duckdns.org`. Crée ton compte : le
premier créé est le tien, et il reçoit d'office la formule Studio — crédits
illimités, toutes les rubriques.

**7. Fermer la porte derrière toi.** Tant que les inscriptions sont ouvertes,
n'importe qui tombant sur l'adresse peut se créer un compte sur ta machine.
Dans *Paramètres*, referme-les. (En ligne de commande, ce serait
`FLAMBEE_SIGNUP=ferme` dans `/opt/flambee/.env` — mais justement, tu n'as pas
de ligne de commande.)

**8. Sur l'écran d'accueil.** *Partager → Sur l'écran d'accueil.* Cette
adresse-là ne changera plus jamais : l'icône restera juste.

### Si quelque chose ne va pas

| Ce que tu vois | Ce que c'est | Quoi faire |
|---|---|---|
| Safari ne se connecte pas, même en `http`, cinq minutes après | Le nom DuckDNS ne pointe pas encore, ou le fichier n'a pas été collé dans le bon champ | Attends deux minutes. Toujours rien : recrée la machine en vérifiant que le contenu est bien dans **Cloud config**, et pas ailleurs |
| La page dit **Installation interrompue** | Une étape a échoué ; la raison est juste en dessous | Le plus fréquent : *DuckDNS a refusé le nom ou le jeton*. Recopie-les et recrée la machine |
| La page reste sur la même étape plus de quinze minutes | Rare, mais l'installation a pu se bloquer | Touche *Voir le détail* : les dernières lignes du journal disent où |
| **Ouvrir Flambée** s'affiche mais `https://` ne répond pas | Le certificat se demande à ce moment-là | Attends une minute et recharge |

Dans tous les cas, **détruire la machine et recommencer ne coûte rien** :
Hetzner facture à l'heure, et une installation ratée revient à quelques
centimes.

### Et Colab, alors ?

| | Colab | Serveur permanent |
|---|---|---|
| Adresse | Nouvelle à chaque lancement | La même, pour toujours |
| Erreurs 530 / 1033 | Possibles, c'est un tunnel | **Impossibles : il n'y a plus de tunnel** |
| Durée de vie | Quelques heures, puis la machine est reprise | Permanente |
| Les vidéos produites | Disparaissent avec la machine | Restent, et sont sauvegardées chaque nuit |
| Un rendu quand l'onglet est fermé | S'arrête | Continue |
| Démarrage | Lancer une cellule, attendre deux minutes | Rien à faire, c'est allumé |
| Téléchargement TikTok/YouTube | Bloqué depuis une IP Google | Un peu moins bloqué depuis un autre hébergeur, mais les plateformes filtrent aussi les centres de données : ne compte pas dessus, l'import de fichiers reste la voie sûre |

Le carnet reste dans le dépôt, et il marche toujours. Il devient le dépannage
du jour où le serveur a un souci — pas l'outil de tous les jours.

### Les corrections arrivent toutes seules

À 4 h 10 chaque nuit, le serveur va voir si la branche a bougé. Si oui, il
reconstruit et redémarre ; sinon il ne touche à rien. Deux garde-fous, parce
qu'une mise à jour automatique qui casse quelque chose est pire que pas de
mise à jour du tout :

- **jamais pendant un rendu.** Le script demande à l'application combien de
  travaux tournent (`/api/health`, champ `travaux_en_cours`) et repart sans
  rien faire s'il y en a. Un travail figé depuis plus d'un quart d'heure n'est
  plus compté : sinon une seule vidéo ratée bloquerait les mises à jour pour
  toujours ;
- **jamais pour rien.** Si le dépôt n'a pas bougé, rien n'est reconstruit —
  la construction coupe le service plusieurs minutes.

Le journal se lit dans `/var/log/flambee-mise-a-jour.log`. Pour ne plus rien
recevoir, commenter la ligne dans `crontab -e`, ou pointer `BRANCHE` sur une
version figée.

### Brancher la vitrine sur l'atelier

Tant que l'application ne tourne nulle part, l'export statique fait descendre
les boutons « Créer un compte » et « Connexion » vers l'essayage : il vaut
mieux montrer le produit que mener à une page morte. Le jour où l'atelier
répond, il reste une commande — sans elle, le site publié continue d'ignorer
qu'un atelier existe.

```bash
python outils/exporter_site.py --sortie export \
    --atelier https://<ton-nom>.duckdns.org
git add export && git commit -m "Brancher la vitrine sur l'atelier" && git push
```

Les quatre adresses de compte (`/inscription`, `/connexion`, `/studio`,
`/mot-de-passe-oublie`) pointent alors vers le serveur, et l'hébergeur de la
vitrine republie tout seul au premier envoi.

Les deux moitiés restent séparées, et c'est voulu : la vitrine est servie
gratuitement depuis un hébergeur de fichiers, où elle encaisse n'importe quelle
affluence, pendant que le serveur ne travaille que pour les gens réellement
inscrits.

### Ce que le fichier fait

Il ouvre les ports 80 et 443 dans le pare-feu local, engendre la clé de
signature des sessions sur la machine — pour qu'elle ne transite par aucun
formulaire —, installe une sauvegarde quotidienne du volume dans
`/var/backups/flambee` en gardant une semaine, et une mise à jour nocturne.

Si les trois champs d'adresse restent vides, il se rabat sur `sslip.io`, qui
ne demande aucune inscription mais partage son quota de certificats entre tous
ses utilisateurs : le HTTPS y échoue certains jours. C'est un dépannage, pas
un choix — il le signale dans le journal.

**Chez Oracle seulement**, il reste une chose à faire à la main : ouvrir aussi
les ports 80 et 443 dans la *Security List* du réseau virtuel, côté console.
Le pare-feu de la machine ne suffit pas, celui du réseau compte aussi. C'est
la cause numéro un d'un site injoignable alors que le conteneur tourne. Chez
Hetzner, il n'y a rien à ouvrir.

### Ce qui a été vérifié, et ce qui ne peut pas l'être

Ce qui l'a été, et qui tourne à chaque exécution de la suite de tests
(`tests/test_deploiement.py`) :

- le fichier est un cloud-init valide, et chacun des quatre scripts qu'il
  embarque passe l'analyse syntaxique de son interpréteur ;
- aucun réglage `FLAMBEE_*` écrit par le déploiement n'est ignoré par le code
  — une faute de frappe y serait invisible autrement ;
- les étapes annoncées à la page d'attente sont celles qu'elle connaît, et
  dans un ordre où la progression ne recule jamais ;
- la page d'attente rend bien le port 80 **avant** que Caddy le réclame, et
  seulement **après** la construction de l'image ;
- la mise à jour nocturne ne reconstruit ni pendant un rendu, ni sans raison.

Le script de mise à jour a été exécuté pour de vrai, contre un dépôt git réel
et une fausse application : neuf cas, du « rien de neuf » au « la nouvelle
version ne redémarre pas ». La page d'attente aussi, dans un navigateur, à
390 px de large.

Ce qui ne l'a pas été : **le fichier n'a jamais tourné sur une vraie
instance**, je n'y ai pas accès. Les chemins d'erreur sont écrits et relus
(adresse DuckDNS valide, jeton refusé, domaine propre, repli), pas exécutés
sur une vraie machine, chez Hetzner ni ailleurs. En cas d'échec, la page d'attente affiche la raison,
et le journal complet se lit dans `/var/log/flambee-installation.log`.

### Si tu passes par Oracle plutôt que Hetzner

Trois différences, et rien d'autre : l'instance à créer est une *Ampere A1*
(ARM) sous Ubuntu 22.04 ou 24.04 avec 2 à 4 cœurs ; le champ où coller le
fichier s'appelle *Show advanced options* → *Paste cloud-init script* ; et il
faut ouvrir les ports dans la *Security List* du réseau virtuel, comme dit
plus haut. Les instances ARM gratuites sont souvent en rupture dans les
régions populaires : essayer une autre région est le premier réflexe.

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

Le nom exact du volume dépend du dossier où le dépôt est cloné : depuis
`/opt/flambee`, c'est `flambee_donnees`. Demande-le plutôt que de le deviner —
Docker crée sans rien dire un volume vide sous un nom inconnu, et la sauvegarde
« réussit » en n'archivant rien :

```bash
VOLUME=$(docker inspect -f \
  '{{range .Mounts}}{{if eq .Destination "/donnees"}}{{.Name}}{{end}}{{end}}' \
  "$(docker compose ps -q flambee)")

# Sauvegarde
docker run --rm -v "$VOLUME":/d:ro -v "$PWD":/sortie alpine \
  tar czf /sortie/flambee-sauvegarde.tar.gz -C /d .

# Vérification : sans la base des comptes, l'archive ne vaut rien
tar tzf flambee-sauvegarde.tar.gz | grep travail/flambee.db
```

Sauvegarde la base **avant** toute mise à jour. Une perte de `flambee.db`, ce
sont tous les comptes perdus.

### Restaurer

Vérifié de bout en bout : volume détruit, archive remontée, comptes et mots de
passe retrouvés. L'application doit être arrêtée pendant l'opération, sinon
elle écrit dans la base qu'on est en train de remplacer.

```bash
docker compose down
docker volume create "$VOLUME"
docker run --rm -v "$VOLUME":/d -v "$PWD":/sauv alpine \
  sh -c 'tar xzf /sauv/flambee-sauvegarde.tar.gz -C /d && chown -R 10001:10001 /d'
docker compose up -d
```

Le `chown` n'est pas décoratif : l'application tourne sous l'utilisateur 10001
et non sous root. Sans lui, elle ne peut pas écrire dans les fichiers restaurés.

L'installation automatique d'Oracle pose déjà une sauvegarde chaque nuit dans
`/var/backups/flambee`, et en garde une semaine.

## Réglages qui comptent

| Variable | Pourquoi |
|---|---|
| `FLAMBEE_SECRET_KEY` | Signe les sessions. La changer déconnecte tout le monde ; la laisser vide en fait engendrer une, mais sur un volume neuf elle changerait à chaque recréation. |
| `FLAMBEE_NICE` | Au-dessus de 0, l'encodage laisse respirer le réseau. Sur une petite machine, `5` à `10` évite que le site devienne injoignable pendant un rendu. |
| `FLAMBEE_WHISPER_MODEL` | `base` par défaut. `small` transcrit mieux mais demande environ trois fois plus de temps processeur. |
| `FLAMBEE_MAX_UPLOAD_MB` | À accorder avec `request_body max_size` dans le `Caddyfile`. |
| `FLAMBEE_INVITE_CODE` | La clé d'accès. Renseignée, elle devient obligatoire pour créer un compte : le site reste visible de tous, mais personne n'entre dans l'atelier sans elle. C'est **une** clé, partagée — elle ne distingue pas les personnes, et la révoquer coupe tout le monde. |
| `FLAMBEE_SMTP_*` | Facultatif, mais recommandé dès qu'on ouvre les inscriptions : sans serveur d'envoi, un utilisateur qui oublie son mot de passe dépend de toi pour retrouver son lien dans `docker compose logs flambee`. Renseigne `HOTE`, `PORT`, `UTILISATEUR`, `MOT_DE_PASSE` et au besoin `EXPEDITEUR`. |

## Monter en charge

L'application est mono-processus par choix : un rendu occupe un cœur pendant
des dizaines de secondes, et plusieurs rendus simultanés se gêneraient de toute
façon. Tant que tu es seul ou à quelques-uns, cela suffit.

Au-delà, l'ordre des travaux est : sortir l'encodage dans une file d'attente
(Redis et des workers séparés), puis déplacer les fichiers vers un stockage
objet. `docs/VENDRE.md` détaille ce chemin.

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
