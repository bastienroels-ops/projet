# Ce qu'il reste à faire pour vendre Flambée

Le site public et le moteur de montage fonctionnent. Ce document liste, sans
enjoliver, ce qui manque encore pour encaisser un premier paiement — et les
pièges qui coûtent cher quand on les découvre trop tard.

## 1. Ce qui existe déjà

- Site public : accueil, fonctionnalités, tarifs, questions, pages légales.
- Formules décrites dans `flambee/plans.py` — prix et limites modifiables à un
  seul endroit.
- Liste d'attente fonctionnelle : les inscriptions atterrissent dans
  `work/liste-attente.jsonl` (`site.entries()` les relit).
- L'atelier, protégé par un mot de passe unique (`FLAMBEE_PASSWORD`).

## 2. Ce qui manque, par ordre de dépendance

### a. Les comptes
Aujourd'hui : un seul mot de passe pour tout le monde. Il faut de vrais
comptes — inscription, connexion, mot de passe haché (argon2 ou bcrypt),
sessions, réinitialisation par e-mail. Cela suppose une base de données
(PostgreSQL ou SQLite selon l'échelle) : le stockage actuel en fichiers JSON ne
tient pas à plusieurs utilisateurs simultanés.

### b. Le cloisonnement
Chaque projet doit appartenir à un compte. Aujourd'hui, quiconque connaît un
identifiant de projet peut le consulter : c'est sans conséquence en usage
personnel, inacceptable dès le premier client.

### c. Les quotas
Les limites annoncées sur la page Tarifs (3, 30, illimité) ne sont pas
appliquées. Il faut compter les rendus par compte et par période, et refuser
proprement au-delà.

### d. Le paiement
Stripe est le chemin le plus court : Checkout pour l'abonnement, le portail
client pour la résiliation, et des webhooks pour synchroniser l'état. Prévoir
la TVA — pour un service numérique vendu à des particuliers dans l'UE, elle est
due dans le pays de l'acheteur (guichet unique OSS).

### e. L'infrastructure de rendu
Un encodage occupe un cœur pendant des dizaines de secondes. À plusieurs
clients simultanés, il faut une file d'attente (Redis + workers) et des
machines séparées du serveur web. Prévoir aussi un stockage objet (S3 ou
équivalent) pour les fichiers, et leur suppression automatique.

## 3. Les points juridiques à ne pas remettre à plus tard

- **Mentions légales obligatoires** : identité de l'éditeur, hébergeur,
  directeur de la publication. Les gabarits sont en place avec les zones
  `[à compléter]` — sans elles, le service est en infraction.
- **Le téléchargement depuis TikTok et YouTube contrevient à leurs conditions
  d'utilisation.** Tolérable pour un usage privé, c'est une tout autre affaire
  dans un service payant : la plateforme peut agir contre l'éditeur, et les
  ayants droit contre la réutilisation des vidéos. Deux voies plus sûres :
  n'accepter que l'import de fichiers, ou n'autoriser que les contenus dont
  l'utilisateur est l'auteur, avec engagement explicite à l'inscription.
- **RGPD** : registre des traitements, sous-traitants (Microsoft pour la voix,
  Anthropic pour les scripts), durées de conservation, procédure d'effacement.
- **Droit de rétractation** : quatorze jours, sauf renoncement exprès pour une
  exécution immédiate — à faire cocher au moment du paiement.
- **Conditions générales de vente**, distinctes des conditions d'utilisation.

## 4. L'ordre que je recommande

1. Valider la demande avec la liste d'attente avant de coder quoi que ce soit.
2. Trancher la question des sources — c'est elle qui détermine si le produit
   est vendable tel quel.
3. Comptes + base de données + cloisonnement.
4. Quotas, puis Stripe.
5. File de rendu, quand les rendus simultanés deviennent un problème réel.
6. Mentions légales complétées **avant** la première mise en ligne publique.

Les points 1 et 2 ne coûtent presque rien et évitent de construire sur du sable.
