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
