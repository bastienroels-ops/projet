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


/* 8. La convergence.
      Trois sources qui se rejoignent en une vidéo verticale, au rythme du
      défilement. Tout passe par des transformations : ni largeur, ni position,
      ni marge ne changent, donc le navigateur n'a jamais à recalculer la mise
      en page — c'est ce qui garde le mouvement fluide. */
(() => {
  const section = document.getElementById("convergence");
  if (!section) return;

  const sources = [...section.querySelectorAll(".source")];
  const montage = section.querySelector(".montage");
  const legende = document.getElementById("scene3d-legende");
  if (!sources.length || !montage) return;

  /* Position de départ de chaque source : en éventail, inclinée, en retrait.
     L'arrivée est commune — le centre — d'où la convergence. */
  const DEPARTS = [
    { x: -230, y: -40, z: -180, ry: 26,  rz: -7 },
    { x:    0, y:  34, z:   40, ry: 0,   rz:  2 },
    { x:  232, y: -26, z: -210, ry: -27, rz:  8 },
  ];

  const RECITS = [
    "Deux à cinq vidéos sur une même thématique.",
    "Les premières secondes de chacune sont notées : la plus percutante ouvre.",
    "Un seul fil, vertical, sous-titré au mot près.",
  ];

  const sobre = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const doux = (t) => t * t * (3 - 2 * t);        // accélère puis décélère
  const entre = (t, a, b) => Math.min(1, Math.max(0, (t - a) / (b - a)));

  let dernierRecit = -1;

  const plateau = section.querySelector(".scene3d-plateau");

  /* L'écart de départ suit la largeur disponible : à 360 px, un éventail
     calibré pour un écran de bureau envoie les sources entièrement hors du
     cadre, et la scène commence sur du vide. */
  function etendue() {
    const large = plateau ? plateau.clientWidth : 620;
    return Math.min(1, Math.max(0.42, large / 620));
  }

  function poser(avance) {
    const ampleur = etendue();
    sources.forEach((source, i) => {
      const depart = DEPARTS[i];
      /* Chaque source part avec un léger décalage : elles ne se rangent pas
         d'un bloc, ce qui serait mécanique. */
      const t = doux(entre(avance, 0.08 + i * 0.06, 0.72 + i * 0.05));
      const x = depart.x * ampleur * (1 - t);
      const y = depart.y * (1 - t);
      const z = depart.z * (1 - t) - 60 * t;
      const ry = depart.ry * (1 - t);
      const rz = depart.rz * (1 - t);
      const echelle = 1 - 0.22 * t;
      source.style.transform =
        `translate(-50%, -50%) translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, `
        + `${z.toFixed(1)}px) rotateY(${ry.toFixed(2)}deg) `
        + `rotateZ(${rz.toFixed(2)}deg) scale(${echelle.toFixed(3)})`;
      // Elles s'effacent en fin de course : c'est le montage qui reste.
      source.style.opacity = String((1 - entre(avance, 0.58 + i * 0.04, 0.86)).toFixed(3));
    });

    const m = doux(entre(avance, 0.42, 0.92));
    montage.style.transform =
      `translate(-50%, -50%) translate3d(0, 0, ${(120 * m).toFixed(1)}px) `
      + `scale(${(0.72 + 0.28 * m).toFixed(3)})`;
    montage.style.opacity = m.toFixed(3);

    if (legende) {
      const index = avance < 0.34 ? 0 : avance < 0.7 ? 1 : 2;
      if (index !== dernierRecit) {
        dernierRecit = index;
        legende.textContent = RECITS[index];
      }
    }
  }

  if (sobre) {                 // pas de récit : on montre l'aboutissement
    poser(1);
    return;
  }

  let enAttente = false;
  function suivre() {
    const cadre = section.getBoundingClientRect();
    const course = Math.max(1, cadre.height - window.innerHeight);
    const avance = Math.min(1, Math.max(0, -cadre.top / course));
    poser(avance);
    enAttente = false;
  }

  /* Le calcul est reporté à la prochaine image : un écouteur de défilement qui
     écrit des styles à chaque événement fait travailler le navigateur deux
     fois pour la même image. */
  function planifier() {
    if (enAttente) return;
    enAttente = true;
    requestAnimationFrame(suivre);
  }

  poser(0);
  window.addEventListener("scroll", planifier, { passive: true });
  window.addEventListener("resize", planifier, { passive: true });
})();
