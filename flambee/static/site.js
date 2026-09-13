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
