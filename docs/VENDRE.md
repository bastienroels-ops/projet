# Vendre Flambée : ce qu'il faut, dans l'ordre

Ce document répond à une question précise : *comment passer d'un outil qui
marche pour moi à un service que des clients paient une vingtaine d'euros par
mois ?*

Il commence par deux obstacles, parce qu'ils décident du reste. Le tutoriel
d'hébergement vient après — il ne sert à rien de monter un serveur pour un
produit qui ne peut pas être vendu tel quel.

---

## 1. Les deux obstacles, mesurés

### a. Les téléchargements TikTok et YouTube ne marchent pas depuis un serveur

Ce n'est pas une opinion. Voici ce que répondent les plateformes quand la
demande vient d'une machine de centre de données — c'est-à-dire de n'importe
quel serveur loué :

| Source | Réponse |
|---|---|
| TikTok | `Unexpected response from webpage request` — échec |
| YouTube Shorts | `Sign in to confirm you're not a bot` — échec |
| YouTube, vidéo longue | passe parfois, échoue souvent |

Les deux plateformes filtrent par réputation d'adresse IP. Depuis un téléphone
ou une connexion domestique, ça passe ; depuis un serveur, non. C'est pour
cette raison exacte que l'atelier tourne aujourd'hui sur Colab avec un tunnel :
la machine de Google est mieux vue que celle d'un hébergeur, et même là, c'est
irrégulier.

**Conséquence directe** : si le produit vendu est « colle des liens TikTok »,
tes clients paient 20 € par mois pour un bouton qui échoue la plupart du temps.
Le rembourser sera la moindre des choses.

Les contournements existent — proxys résidentiels, cookies de session
recopiés — et ils ont tous le même défaut : ils coûtent cher, cassent sans
prévenir, et aggravent le point suivant.

### b. Vendre l'accès change la nature du risque juridique

Télécharger une vidéo TikTok pour soi contrevient déjà aux conditions de la
plateforme. C'est toléré en pratique tant que ça reste privé.

Facturer l'accès à un outil qui le fait, c'est autre chose :

- **Tu deviens éditeur d'un service commercial** bâti sur une violation
  explicite des conditions de TikTok et de YouTube. Ces plateformes agissent
  contre ce genre de service, et l'historique est fourni.
- **Les vidéos sources appartiennent à leurs auteurs.** Tes clients
  publieraient des montages faits à partir du travail d'autrui, sous leur
  propre nom, à des fins commerciales. Le risque ne s'arrête pas à toi : il
  retombe sur eux, et ils se retourneront vers toi.
- Ton prestataire de paiement posera la question. Stripe demande ce que fait le
  service, et suspend les comptes dont l'activité l'expose.

### c. Ce qui reste, et qui vaut la peine

Retire les liens TikTok, et il reste **tout le reste** — c'est-à-dire le
travail réel :

> Le client dépose ses propres vidéos. Flambée détecte les changements de
> plan, coupe au bon endroit, écrit et pose la voix, cale les sous-titres au
> mot près, mixe la musique sous la parole, et rend un 1080 × 1920 prêt à
> publier. En une passe, sans logiciel à installer.

Ce produit-là est légal, il fonctionne depuis un serveur, et c'est celui qui a
de la valeur : personne ne paie 20 € pour un téléchargeur — il en existe des
dizaines de gratuits — on paie pour le montage.

**Et il est déjà construit.** La route d'import existe
(`POST /api/projects/{id}/uploads`), l'atelier propose déjà « Importe des
vidéos déjà enregistrées sur l'appareil », et le parcours complet a été
vérifié dans le conteneur. Vendre cette version ne demande aucun code
supplémentaire — seulement de retirer le champ des liens, ou de le réserver
aux vidéos dont le client est l'auteur, avec engagement à l'inscription.

---

## 2. Ce qui est déjà fait

Contrairement à ce que disait l'ancienne version de ce document, l'essentiel
est en place :

| | État |
|---|---|
| Comptes (inscription, connexion, mot de passe haché, réinitialisation) | fait — `flambee/users.py`, `auth.py` |
| Cloisonnement : un projet appartient à un compte | fait — `store.get(id, owner=…)` sur toutes les routes |
| Quotas mensuels par formule | fait — `flambee/account.py` |
| Formules et prix | fait — `flambee/plans.py`, modifiables à un seul endroit |
| Image Docker, HTTPS, sauvegarde nocturne | fait — `Dockerfile`, `deploiement/oracle-cloud-init.yaml` |
| **Paiement** | **manquant** |
| **Mentions légales complétées** | **manquant** — zones `[à compléter]` dans `flambee/site.py` |
| File d'attente de rendu | pas nécessaire avant une trentaine de clients |

---

## 3. Le serveur

### Quelle machine

Un rendu occupe un cœur pendant une vingtaine de secondes pour une vidéo d'une
demi-minute (mesuré : 29 secondes de vidéo, 16 secondes d'encodage). La
transcription Whisper est du même ordre. Le web, lui, ne consomme rien.

| Clients | Machine | Ordre de prix |
|---|---|---|
| 1 à 10 | 2 cœurs, 4 Go | Oracle *Always Free* : 0 € — ou 5 à 8 €/mois chez un hébergeur européen |
| 10 à 40 | 4 cœurs, 8 Go | 10 à 20 €/mois |
| au-delà | séparer le web et les rendus | voir §6 |

Vérifie les prix du jour : ils bougent. Les hébergeurs européens à regarder
sont Hetzner, Scaleway et OVH — rester dans l'UE simplifie le RGPD, puisque les
vidéos de tes clients sont des données personnelles.

**Sur l'offre gratuite d'Oracle** : elle suffit techniquement pour démarrer,
mais c'est un service gratuit — il peut être suspendu, et il n'y a personne à
appeler. Pour des clients qui paient, prends une machine payante. Dix euros
par mois, c'est un demi-client.

### L'installation, depuis un iPhone

Tout est déjà écrit. Le détail complet est dans
[`HEBERGEMENT.md`](HEBERGEMENT.md) ; voici la ligne directrice.

1. **Un nom de domaine.** `flambee.online` est déjà à toi : chez ton
   registraire, crée un enregistrement `A` qui pointe `app.flambee.online`
   vers l'adresse IP du serveur. Garde `flambee.online` pour la vitrine
   statique — elle est gratuite et rapide, autant la laisser où elle est.
2. **Le serveur.** Crée la machine sous Ubuntu 24.04. Chez la plupart des
   hébergeurs, le formulaire de création propose un champ *cloud-init* ou
   *script de démarrage* : colle-y
   [`deploiement/oracle-cloud-init.yaml`](../deploiement/oracle-cloud-init.yaml)
   après avoir renseigné le domaine en haut. La machine installe Docker,
   construit l'image, ouvre le pare-feu, obtient le certificat HTTPS et met en
   place la sauvegarde nocturne. Compter une dizaine de minutes.
3. **Le premier compte.** Le premier créé est le tien, et reçoit d'office la
   formule Studio. Referme aussitôt les inscriptions libres si tu veux
   contrôler qui entre : `FLAMBEE_SIGNUP=ferme` dans `/opt/flambee/.env`.
4. **Brancher la vitrine.** Republie le site statique en lui donnant l'adresse
   de l'atelier, pour que les boutons « Créer un compte » y mènent :

   ```bash
   python outils/exporter_site.py --sortie export \
       --atelier https://app.flambee.online --sans-code
   ```

   `--sans-code` retire la porte : une vitrine commerciale doit être visible de
   tout le monde, c'est l'atelier qui est protégé, par de vrais comptes.

### Ce qu'il faut régler en plus pour des clients payants

| Réglage | Pourquoi |
|---|---|
| `FLAMBEE_SECRET_KEY` | Sans elle, un redémarrage déconnecte tous tes clients. |
| `FLAMBEE_SMTP_*` | Un client qui oublie son mot de passe doit pouvoir le réinitialiser seul. Sans serveur d'envoi, il dépend de toi — et tu ne dors pas la nuit. |
| `ANTHROPIC_API_KEY` | L'écriture des scripts. Facturée à l'usage : c'est ton coût variable, surveille-le. |
| `FLAMBEE_NICE=5` | L'encodage laisse respirer le web. Sans ça, le site devient injoignable pendant un rendu. |

---

## 4. Le paiement

Stripe est le chemin le plus court, et il n'est pas encore branché. Ce qu'il
faut écrire, dans l'ordre :

1. **Stripe Checkout** pour l'abonnement : un bouton par formule, qui crée une
   session et redirige. Une trentaine de lignes.
2. **Les webhooks** : c'est Stripe qui fait autorité sur l'état d'un
   abonnement, pas ta base. Écoute `checkout.session.completed`,
   `customer.subscription.updated` et `.deleted`, et range la formule du compte
   en conséquence. **Vérifie la signature du webhook** — sans elle, n'importe
   qui peut s'offrir la formule Studio en envoyant une requête.
3. **Le portail client** de Stripe pour la résiliation et le changement de
   carte. Une page à ne pas écrire.
4. **La TVA.** Pour un service numérique vendu à des particuliers dans l'UE,
   elle est due dans le pays de l'acheteur. Stripe Tax le calcule ; le guichet
   unique (OSS) sert à la déclarer. À régler avant le premier client, pas après.

Dis-le-moi et je le construis : c'est une demi-journée, garde-fous compris.

---

## 5. Le juridique, avant le premier euro

Rien de tout cela n'est optionnel en France.

- **Une structure.** Micro-entreprise suffit pour commencer et se crée en
  ligne. Sans elle, tu ne peux pas facturer.
- **Mentions légales.** Identité de l'éditeur, hébergeur, directeur de la
  publication. Les zones `[à compléter]` de `flambee/site.py` attendent. Les
  publier vides est une infraction.
- **Des CGV**, distinctes des conditions d'utilisation : prix, durée,
  résiliation, remboursement.
- **Droit de rétractation** : quatorze jours, sauf renoncement exprès pour une
  exécution immédiate — à faire cocher au moment du paiement.
- **RGPD** : tu héberges les vidéos de tes clients et leurs adresses. Registre
  des traitements, sous-traitants déclarés (Microsoft pour la voix, Anthropic
  pour les scripts), durée de conservation, procédure d'effacement.
- **L'engagement sur les sources**, si tu gardes le champ des liens : une case
  à cocher à l'inscription, où le client déclare n'utiliser que des vidéos dont
  il détient les droits. Cela ne te protège pas de tout, mais cela déplace la
  responsabilité et montre la bonne foi.

---

## 6. Ce qui cassera en premier

Dans cet ordre, d'expérience :

1. **Le disque.** Les vidéos rendues s'accumulent — compte une centaine de Mo
   par client et par mois. Efface automatiquement au bout de quelques semaines,
   et préviens-en dans les CGV.
2. **Les rendus simultanés.** L'application est mono-processus par choix. Au
   troisième client qui rend en même temps, le troisième attend. Vers une
   trentaine de clients, sors l'encodage dans une file d'attente (Redis et des
   workers séparés).
3. **La facture Anthropic.** C'est ton seul coût qui grandit avec l'usage.
   Mets une alerte de dépense dès le premier jour.
4. **Toi.** Le support d'un service payant, c'est des messages le dimanche.
   Prévois une adresse de contact, et une réponse type.

---

## L'ordre que je recommande

1. **Tranche la question des sources.** Tout en dépend, et c'est gratuit :
   c'est une décision, pas du code.
2. Complète les mentions légales et crée la structure.
3. Prends une machine payante et installe (§3) — une heure.
4. Branche Stripe (§4).
5. Ouvre à cinq clients choisis, gratuitement, pendant un mois. Ce qu'ils
   casseront t'apprendra plus que trois semaines de préparation.
6. Alors seulement, fais payer.
