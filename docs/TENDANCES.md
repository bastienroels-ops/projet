# Trend Finder — d'où viennent les données, et comment aller plus loin

Cette page répond à une question précise : *pourquoi le Trend Finder ne
cherche-t-il pas directement par mots-clés sur tout TikTok, et que faut-il
pour que ça devienne possible ?*

## Pourquoi ce n'est pas déjà fait

Il n'existe pas d'API TikTok tierce, gratuite et immédiate qui permette de
chercher des vidéos par mots-clés ou hashtags sur l'ensemble de la
plateforme. Grattage du site excepté — explicitement exclu ici, parce que ça
contourne les protections de TikTok et casse au moindre changement de mise en
page — les chemins légaux sont au nombre de deux, et aucun des deux ne se
branche en cinq minutes :

### 1. TikTok Research API

L'API officielle de TikTok pour la recherche. Elle permet d'interroger des
vidéos publiques par mots-clés, hashtags et période, avec vues, likes,
commentaires, partages, date de publication.

Ce qui bloque : l'accès est réservé aux **chercheurs qualifiés** (universités
et organismes à but non lucratif), l'usage doit rester **non commercial**, et
l'ouverture géographique est partielle (essentiellement États-Unis, avec des
extensions en Europe sous condition). Un produit commercial comme Flambée
n'est en général pas éligible tel quel.

Si tu obtiens malgré tout un accès (par exemple via un partenariat
académique), l'intégration se branche dans `flambee/trends.py` comme une
nouvelle fonction de récupération, appelée depuis la route
`POST /api/tendances/recherche` en plus du mode « liens collés » — sans rien
changer à l'interface ni à la base de données, déjà prêtes pour recevoir plus
de vidéos par recherche.

### 2. Un fournisseur de données tiers sous licence

Des sociétés (Exolyt, Kalodata, Analisa.io, et d'autres) collectent des
données TikTok via leurs propres accords et les revendent par API,
généralement sur abonnement. C'est un chemin légal — c'est leur métier — mais
payant, et la fiabilité/couverture varie d'un fournisseur à l'autre.

Si tu choisis d'en payer un : donne-moi le nom du service et une clé d'accès,
et l'intégration se construit exactement comme au point 1.

## Ce qui fonctionne déjà, sans rien configurer

Le mode par défaut : tu repères toi-même des vidéos (recherche normale sur
TikTok, un hashtag, un compte), tu colles leurs liens dans l'onglet
**Résultats**. Flambée récupère alors leurs vraies statistiques publiques via
`yt-dlp` — la même brique que l'étape 1 du montage utilise déjà — et applique
dessus :

- le tri (vues, récence, engagement, tendances émergentes) ;
- le repérage de « Pépites » (vitesse de vues, et croissance réellement
  mesurée dès qu'une vidéo est revue une seconde fois) ;
- l'analyse d'une vidéo (plans, rythme, transcription si la formule le
  permet) ;
- l'inspiration (une idée originale via l'API Claude, jamais une copie) ;
- les sauvegardes et « Mes tendances » ;
- l'observation des hashtags qui reviennent dans **tes propres recherches**
  (pas un classement TikTok global — aucune source ne le fournit
  aujourd'hui).

Rien de tout cela n'invente de statistique : un champ que la plateforme n'a
pas communiqué reste vide côté interface, jamais estimé.

## La veille (surveillance automatique)

L'architecture existe (`flambee/trends.py` : tables `tendance_surveillances`,
fonctions `creer_surveillance`, `verifier_surveillances`), mais rien ne
l'active : `verifier_surveillances()` n'est appelée par aucun ordonnanceur.
Pour la brancher un jour, il suffit d'un cron (ou d'une tâche planifiée) qui
appelle cette fonction à intervalle régulier — aucune autre modification
n'est nécessaire.
