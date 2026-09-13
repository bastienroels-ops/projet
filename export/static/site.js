/* Flambée — le site public. Trois comportements, rien de plus. */

/* 1. Le menu sur téléphone. */
const bouton = document.getElementById("menu-bouton");
const nav = document.getElementById("site-nav");
if (bouton && nav) {
  bouton.addEventListener("click", () => {
    const ouvert = nav.classList.toggle("ouvert");
    bouton.setAttribute("aria-expanded", String(ouvert));
  });
}

/* 2. Les échantillons vidéo ne se chargent qu'une fois visibles : une page
      d'accueil ne doit pas tirer sept vidéos d'un coup sur un forfait mobile. */
const paresseuses = document.querySelectorAll("video[data-src]");
if (paresseuses.length) {
  const charger = (video) => {
    if (video.dataset.charge) return;
    video.dataset.charge = "1";
    video.src = video.dataset.src;
    video.play().catch(() => {});
  };
  if ("IntersectionObserver" in window) {
    const observateur = new IntersectionObserver((entrees) => {
      entrees.forEach((entree) => {
        if (entree.isIntersecting) {
          charger(entree.target);
          observateur.unobserve(entree.target);
        }
      });
    }, { rootMargin: "250px" });
    paresseuses.forEach((v) => observateur.observe(v));
  } else {
    paresseuses.forEach(charger);
  }
}

/* 3. La bascule mensuel / annuel sur les tarifs. */
const bascule = document.querySelector(".bascule-facturation");
if (bascule) {
  bascule.addEventListener("click", (evenement) => {
    const cible = evenement.target.closest("button");
    if (!cible) return;
    bascule.querySelectorAll("button").forEach((b) => b.classList.remove("actif"));
    cible.classList.add("actif");

    const annuel = cible.dataset.periode === "annuel";
    document.querySelectorAll(".tarif-prix b[data-mensuel]").forEach((prix) => {
      prix.textContent = `${annuel ? prix.dataset.annuel : prix.dataset.mensuel} €`;
    });
    document.querySelectorAll(".tarif-prix span").forEach((mention) => {
      mention.textContent = annuel ? "par mois, facturé à l'année" : "par mois";
    });
  });
}


/* 4. Révélation au défilement.
      Chaque bloc se pose légèrement quand il entre à l'écran. L'effet est
      désactivé si le système demande de réduire les animations. */
const menage = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const aReveler = document.querySelectorAll(
  ".section > .eyebrow, .section > h1, .section > h2, .section-accroche," +
  " .atout, .etapes li, .style-carte, .tarif, .faq details, .appel > *," +
  " .hero-texte > *, .telephone, .bande p");

if (aReveler.length && !menage && "IntersectionObserver" in window) {
  aReveler.forEach((element, index) => {
    element.classList.add("a-reveler");
    // Un léger décalage entre voisins : l'ensemble se pose en cascade.
    element.style.setProperty("--retard", `${(index % 6) * 60}ms`);
  });
  const observateur = new IntersectionObserver((entrees) => {
    entrees.forEach((entree) => {
      if (entree.isIntersecting) {
        entree.target.classList.add("revele");
        observateur.unobserve(entree.target);
      }
    });
  }, { rootMargin: "-40px 0px -60px 0px" });
  aReveler.forEach((element) => observateur.observe(element));
}

/* 5. L'en-tête se densifie dès qu'on quitte le haut de page. */
const entete = document.querySelector(".site-header");
if (entete) {
  const ajuster = () => entete.classList.toggle("pose", window.scrollY > 12);
  ajuster();
  window.addEventListener("scroll", ajuster, { passive: true });
}


/* 6. Essayage des sous-titres.
      Le visiteur tape sa phrase, choisit une écriture, et reçoit un clip rendu
      par ffmpeg. Chaque frappe ne doit pas déclencher un encodage : on attend
      que la saisie se pose, et l'on n'envoie que si quelque chose a changé. */
(() => {
  const bloc = document.getElementById("essayage");
  if (!bloc) return;

  const champ = document.getElementById("essayage-texte");
  const video = document.getElementById("essayage-video");
  const legende = document.getElementById("essayage-legende");
  const etat = document.getElementById("essayage-etat");
  const compteur = document.getElementById("essayage-compteur");
  const puces = [...bloc.querySelectorAll(".puce")];
  const MAX = parseInt(champ.getAttribute("maxlength"), 10) || 70;

  let descriptions = {};
  try {
    descriptions = JSON.parse(
      document.getElementById("styles-json")?.textContent || "{}");
  } catch (e) { /* la légende restera celle du gabarit */ }

  let style = puces[0]?.dataset.style || "punch";
  let dernier = "";
  let minuterie = null;
  let demande = 0;

  /* Version figée : le site public peut être publié en pages statiques, sans
     Python ni ffmpeg derrière. L'essayage libre demande un serveur ; on garde
     alors les six clips déjà calculés, que les puces font défiler. Le bloc
     reste vivant au lieu de disparaître. */
  const fige = bloc.dataset.fige === "1";
  if (fige) {
    const champBloc = bloc.querySelector(".essayage-champ");
    if (champBloc) champBloc.hidden = true;
    const montrer = () => {
      video.removeAttribute("poster");
      video.src = `${bloc.dataset.echantillons || "media"}/${style}-sample.mp4`;
      video.play().catch(() => {});
      if (legende && descriptions[style]) legende.textContent = descriptions[style];
    };
    puces.forEach((puce) => {
      puce.addEventListener("click", () => {
        puces.forEach((p) => {
          p.classList.toggle("actif", p === puce);
          p.setAttribute("aria-selected", p === puce ? "true" : "false");
        });
        style = puce.dataset.style;
        montrer();
      });
    });
    montrer();
    return;
  }

  const majCompteur = () => {
    compteur.textContent = `${champ.value.length}/${MAX}`;
    compteur.classList.toggle("proche", champ.value.length > MAX - 10);
  };

  async function rendre() {
    const texte = champ.value.trim();
    if (!texte) {
      etat.textContent = "Écris une phrase pour voir le rendu.";
      return;
    }
    const cle = `${style}::${texte}`;
    if (cle === dernier) return;            // rien n'a changé
    dernier = cle;

    const jeton = ++demande;                // seule la dernière réponse compte
    bloc.classList.add("charge");
    etat.textContent = "Rendu en cours…";

    const url = `/api/essayage?texte=${encodeURIComponent(texte)}`
      + `&style=${encodeURIComponent(style)}`;
    try {
      const reponse = await fetch(url);
      if (jeton !== demande) return;
      if (!reponse.ok) {
        const detail = await reponse.json().catch(() => ({}));
        etat.textContent = reponse.status === 429
          ? "Trop d'essais d'un coup — laisse passer une minute."
          : (detail.detail || "Rendu impossible pour l'instant.");
        dernier = "";                        // pour pouvoir réessayer
        bloc.classList.remove("charge");
        return;
      }
      /* On passe par un blob : la vidéo ne clignote pas entre deux rendus,
         et l'on sait précisément quand l'image est prête. */
      const blob = await reponse.blob();
      if (jeton !== demande) return;
      const ancienne = video.src;
      /* L'affiche porte la phrase par défaut : une fois qu'un vrai rendu
         existe, la laisser reviendrait à réafficher un texte que le visiteur
         n'a pas écrit à chaque changement de source. */
      video.removeAttribute("poster");
      video.src = URL.createObjectURL(blob);
      video.play().catch(() => {});
      if (ancienne.startsWith("blob:")) URL.revokeObjectURL(ancienne);
      etat.textContent = "";
    } catch (erreur) {
      if (jeton === demande) {
        etat.textContent = "Rendu indisponible — réessaie dans un instant.";
        dernier = "";
      }
    } finally {
      if (jeton === demande) bloc.classList.remove("charge");
    }
  }

  const differer = (delai = 650) => {
    clearTimeout(minuterie);
    minuterie = setTimeout(rendre, delai);
  };

  champ.addEventListener("input", () => { majCompteur(); differer(); });
  champ.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(minuterie); rendre(); }
  });

  puces.forEach((puce) => {
    puce.addEventListener("click", () => {
      puces.forEach((p) => {
        p.classList.toggle("actif", p === puce);
        p.setAttribute("aria-selected", p === puce ? "true" : "false");
      });
      style = puce.dataset.style;
      const decrit = descriptions[style];
      if (decrit && legende) legende.textContent = decrit;
      clearTimeout(minuterie);
      rendre();
    });
  });

  majCompteur();
  /* Le premier rendu attend que la section approche de l'écran : une page
     d'accueil n'a pas à lancer un encodage pour un visiteur qui ne descendra
     jamais jusque-là. */
  if ("IntersectionObserver" in window) {
    const oeil = new IntersectionObserver((entrees) => {
      if (entrees.some((e) => e.isIntersecting)) {
        oeil.disconnect();
        rendre();
      }
    }, { rootMargin: "200px" });
    oeil.observe(bloc);
  } else {
    rendre();
  }
})();


/* 7. Finitions.
      Trois effets qui ne changent rien au contenu mais donnent sa matière à
      la page. Tous s'effacent si le système demande de réduire les animations. */
(() => {
  const sobre = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* a. La barre de progression de lecture. */
  const barre = document.getElementById("progression");
  if (barre) {
    const avancer = () => {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      const part = total > 0 ? (window.scrollY / total) * 100 : 0;
      barre.style.setProperty("--avance", `${Math.min(100, part).toFixed(2)}%`);
    };
    avancer();
    window.addEventListener("scroll", avancer, { passive: true });
    window.addEventListener("resize", avancer, { passive: true });
  }

  /* b. Le halo qui suit le curseur sur les boutons pleins. On ne l'installe
        que sur les appareils à pointeur fin : sur un écran tactile, il n'y a
        pas de survol, et l'écouteur ne servirait qu'à consommer. */
  if (!sobre && window.matchMedia("(pointer: fine)").matches) {
    document.querySelectorAll(".button.primary, button.primary").forEach((el) => {
      el.addEventListener("pointermove", (e) => {
        const cadre = el.getBoundingClientRect();
        el.style.setProperty("--x", `${e.clientX - cadre.left}px`);
        el.style.setProperty("--y", `${e.clientY - cadre.top}px`);
      });
    });
  }

  /* c. Les chiffres se posent quand la bande entre à l'écran. On ne compte
        que ce qui est un nombre : « 9:16 » et « 1080p » restent tels quels. */
  const chiffres = document.querySelectorAll(".chiffres-grille b");
  if (chiffres.length && !sobre && "IntersectionObserver" in window) {
    const compter = (element) => {
      const texte = element.textContent.trim();
      const cible = parseInt(texte, 10);
      if (!Number.isFinite(cible) || !/^\d+$/.test(texte)) return;
      const duree = 900;
      const depart = performance.now();
      const pas = (instant) => {
        const part = Math.min(1, (instant - depart) / duree);
        /* Décélération : le compteur ralentit en approchant, comme un
           compteur mécanique qui se cale. */
        const douceur = 1 - Math.pow(1 - part, 3);
        element.textContent = String(Math.round(cible * douceur));
        if (part < 1) requestAnimationFrame(pas);
      };
      requestAnimationFrame(pas);
    };
    const oeil = new IntersectionObserver((entrees) => {
      entrees.forEach((entree) => {
        if (entree.isIntersecting) {
          compter(entree.target);
          oeil.unobserve(entree.target);
        }
      });
    }, { rootMargin: "-40px" });
    chiffres.forEach((c) => oeil.observe(c));
  }
})();


/* 8. La séquence.
      Cinq actes dans un seul décor : les sources arrivent, la meilleure
      accroche est retenue, le style se pose, le script s'écrit, le fichier
      sort. Le défilement sert d'horloge.

      Chaque carte est décrite par une position à chaque acte ; le script
      interpole entre les deux positions qui encadrent l'instant courant. Cette
      forme se relit et s'étend — ajouter un acte, c'est ajouter une ligne —
      là où une suite de conditions imbriquées deviendrait illisible au
      troisième. */
(() => {
  const section = document.getElementById("sequence");
  if (!section) return;

  const plateau = section.querySelector(".sequence-plateau");
  const sources = [...section.querySelectorAll(".source")];
  const montage = section.querySelector(".montage");
  const etapes = [...section.querySelectorAll(".sequence-liste li")];
  if (!plateau || !montage || sources.length !== 3) return;

  // Bornes des cinq actes, en fraction de la course totale.
  const ACTES = [0, 0.20, 0.40, 0.60, 0.80, 1];

  /* Une position par acte, pour chacune des trois sources.
     x, y en pixels ; z en profondeur ; ry, rz en degrés ; e = échelle ;
     o = opacité. */
  const POSES = [
    [ // Source 1 — la moins bien notée : elle s'efface après le choix.
      { x: -236, y: -42, z: -190, ry:  26, rz: -7, e: 1,    o: 1 },
      { x: -210, y:   0, z:  -40, ry:  10, rz:  0, e: .92,  o: 1 },
      { x: -250, y:  20, z: -220, ry:  22, rz: -4, e: .78,  o: .28 },
      { x: -270, y:  40, z: -320, ry:  26, rz: -6, e: .68,  o: 0 },
      { x: -270, y:  40, z: -320, ry:  26, rz: -6, e: .68,  o: 0 },
    ],
    [ // Source 2 — la mieux notée : elle devient le montage.
      { x:    0, y:  36, z:   40, ry:   0, rz:  2, e: 1,    o: 1 },
      { x:    0, y:   0, z:   60, ry:   0, rz:  0, e: 1,    o: 1 },
      { x:    0, y:   0, z:  120, ry:   0, rz:  0, e: 1.16, o: 1 },
      { x:    0, y:   0, z:  120, ry:   0, rz:  0, e: 1.16, o: 0 },
      { x:    0, y:   0, z:  120, ry:   0, rz:  0, e: 1.16, o: 0 },
    ],
    [ // Source 3
      { x:  238, y: -28, z: -220, ry: -27, rz:  8, e: 1,    o: 1 },
      { x:  210, y:   0, z:  -40, ry: -10, rz:  0, e: .92,  o: 1 },
      { x:  250, y:  20, z: -240, ry: -22, rz:  5, e: .78,  o: .28 },
      { x:  270, y:  40, z: -330, ry: -26, rz:  7, e: .68,  o: 0 },
      { x:  270, y:  40, z: -330, ry: -26, rz:  7, e: .68,  o: 0 },
    ],
  ];

  // Le montage n'apparaît qu'au quatrième acte, à la place de la source 2.
  const MONTAGE = [
    { z: 0,   e: .70, o: 0 },
    { z: 0,   e: .70, o: 0 },
    { z: 20,  e: .82, o: 0 },
    { z: 120, e: 1,   o: 1 },
    { z: 130, e: 1.04, o: 1 },
  ];

  const doux = (t) => t * t * (3 - 2 * t);
  const entre = (t, a, b) => Math.min(1, Math.max(0, (t - a) / (b - a)));
  const melange = (a, b, t) => a + (b - a) * t;

  /* L'écart latéral suit la largeur disponible : à 360 px, un éventail calibré
     pour un écran de bureau envoie les cartes hors du cadre, et la scène
     commence sur du vide. */
  function ampleur() {
    return Math.min(1, Math.max(0.40, plateau.clientWidth / 620));
  }

  /* Où en est-on ? Retourne l'acte courant et l'avancée dans cet acte. */
  function situer(avance) {
    for (let i = 0; i < ACTES.length - 1; i++) {
      if (avance < ACTES[i + 1] || i === ACTES.length - 2) {
        return { acte: i, part: doux(entre(avance, ACTES[i], ACTES[i + 1])) };
      }
    }
    return { acte: 0, part: 0 };
  }

  let acteAffiche = -1;

  function poser(avance) {
    const { acte, part } = situer(avance);
    const large = ampleur();

    sources.forEach((source, i) => {
      const de = POSES[i][acte];
      const vers = POSES[i][Math.min(acte + 1, POSES[i].length - 1)];
      const x = melange(de.x, vers.x, part) * large;
      const y = melange(de.y, vers.y, part);
      const z = melange(de.z, vers.z, part);
      const ry = melange(de.ry, vers.ry, part);
      const rz = melange(de.rz, vers.rz, part);
      const e = melange(de.e, vers.e, part);
      source.style.transform =
        `translate(-50%, -50%) translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, `
        + `${z.toFixed(1)}px) rotateY(${ry.toFixed(2)}deg) `
        + `rotateZ(${rz.toFixed(2)}deg) scale(${e.toFixed(3)})`;
      source.style.opacity = melange(de.o, vers.o, part).toFixed(3);
    });

    const de = MONTAGE[acte];
    const vers = MONTAGE[Math.min(acte + 1, MONTAGE.length - 1)];
    montage.style.transform =
      `translate(-50%, -50%) translate3d(0, 0, `
      + `${melange(de.z, vers.z, part).toFixed(1)}px) `
      + `scale(${melange(de.e, vers.e, part).toFixed(3)})`;
    montage.style.opacity = melange(de.o, vers.o, part).toFixed(3);

    if (acte !== acteAffiche) {
      acteAffiche = acte;
      /* Une classe sur la section pilote tout ce qui n'a pas à être interpolé
         image par image : notes, verdict, lignes de script, sous-titre. Le CSS
         s'en charge, avec ses propres transitions. */
      section.className = section.className.replace(/\bacte-\d\b/g, "").trim()
        + ` acte-${acte}`;
      etapes.forEach((li, i) => li.classList.toggle("actif", i === acte));
    }
  }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    poser(1);
    etapes.forEach((li) => li.classList.add("actif"));
    return;
  }

  let enAttente = false;
  function suivre() {
    const cadre = section.getBoundingClientRect();
    const course = Math.max(1, cadre.height - window.innerHeight);
    poser(Math.min(1, Math.max(0, -cadre.top / course)));
    enAttente = false;
  }

  /* Le calcul est reporté à la prochaine image : un écouteur qui écrit des
     styles à chaque événement de défilement fait travailler le navigateur
     deux fois pour la même image. */
  function planifier() {
    if (enAttente) return;
    enAttente = true;
    requestAnimationFrame(suivre);
  }

  poser(0);
  window.addEventListener("scroll", planifier, { passive: true });
  window.addEventListener("resize", planifier, { passive: true });
})();


/* ---------------------------------------------- Le test de l'accroche ------
   Trois manches de deux boutons. Un clic verrouille la manche, ouvre les deux
   verdicts et compte le point. Tout le contenu est déjà dans la page : sans
   JavaScript, les six accroches et leurs raisons restent lisibles, le test
   devient une simple liste commentée. */
(function () {
  "use strict";
  const manches = [...document.querySelectorAll(".duel-manche")];
  if (!manches.length) return;

  const bilan = document.getElementById("duel-bilan");
  const final = document.getElementById("duel-final");
  let jouees = 0;
  let points = 0;

  // Tant que le test n'est pas joué, les verdicts sont repliés : on les cache
  // aussi aux lecteurs d'écran, sinon la réponse est lue avant la question.
  manches.forEach((manche) => {
    manche.querySelectorAll(".duel-verdict, .duel-badge")
      .forEach((e) => e.setAttribute("aria-hidden", "true"));

    manche.querySelectorAll(".duel-choix").forEach((bouton) => {
      bouton.addEventListener("click", () => {
        if (manche.classList.contains("jouee")) return;
        manche.classList.add("jouee");
        bouton.classList.add("choisi");
        manche.querySelectorAll(".duel-verdict, .duel-badge")
          .forEach((e) => e.removeAttribute("aria-hidden"));
        manche.querySelectorAll(".duel-choix").forEach((b) => {
          b.setAttribute("aria-disabled", "true");
          b.style.cursor = "default";
        });
        jouees++;
        if (bouton.dataset.gagnante === "oui") points++;
        annoncer(bouton.dataset.gagnante === "oui");
      });
    });
  });

  const VERDICTS = [
    "Zéro sur trois. Rassure-toi : c'est exactement le tri que Flambée fait " +
      "à ta place, sur tes propres rushes.",
    "Un sur trois. L'accroche qui retient n'est presque jamais celle qui " +
      "annonce le sujet.",
    "Deux sur trois. Tu as l'instinct — Flambée a la patience de le faire " +
      "sur chaque source, à chaque montage.",
    "Trois sur trois. Tu as l'œil. Reste le temps de monter : c'est là que " +
      "Flambée entre en scène.",
  ];

  function annoncer(juste) {
    if (jouees < manches.length) {
      bilan.innerHTML = juste
        ? "Bien vu. <b>" + points + " / " + jouees + "</b>"
        : "Raté — la raison est sous les deux phrases. <b>"
          + points + " / " + jouees + "</b>";
      return;
    }
    bilan.innerHTML = "<b>" + points + " / " + manches.length + "</b> — "
      + VERDICTS[points];
    if (final) final.hidden = false;
  }
})();
