/* La coquille de l'application : un tiroir, rien de plus.
   Le tout est enfermé dans une fonction : chaque page ajoute ses propres
   scripts, et deux « const » de même nom au niveau global se télescopent. */
(() => {
  const bouton = document.getElementById("tiroir-bouton");
  const tiroir = document.getElementById("tiroir");
  const voile = document.getElementById("voile");
  if (!bouton || !tiroir || !voile) return;

  const basculer = (ouvrir) => {
    tiroir.classList.toggle("ouvert", ouvrir);
    voile.classList.toggle("visible", ouvrir);
    bouton.setAttribute("aria-expanded", String(ouvrir));
    document.body.classList.toggle("fige", ouvrir);
  };

  bouton.addEventListener("click",
    () => basculer(!tiroir.classList.contains("ouvert")));
  voile.addEventListener("click", () => basculer(false));
  // Sur téléphone, choisir une rubrique referme le tiroir.
  tiroir.querySelectorAll("a").forEach((lien) =>
    lien.addEventListener("click", () => basculer(false)));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") basculer(false);
  });
})();

/* La grille des créations : l'affiche s'affiche tout de suite, la vidéo ne se
   charge qu'au survol — quarante lecteurs chargés d'emblée rendraient la page
   inutilisable sur une connexion mobile. */
(() => {
  const cartes = document.querySelectorAll(".creation.terminee");
  if (!cartes.length) return;
  if (!window.matchMedia("(pointer: fine)").matches) return;   // pas de survol

  cartes.forEach((carte) => {
    const video = carte.querySelector("video[data-src]");
    if (!video) return;
    let horloge = null;

    carte.addEventListener("pointerenter", () => {
      /* Un survol de passage ne doit pas déclencher un téléchargement : on
         attend que l'intention se confirme. */
      horloge = setTimeout(() => {
        if (!video.src) video.src = video.dataset.src;
        video.play().then(() => video.classList.add("joue")).catch(() => {});
      }, 220);
    });

    carte.addEventListener("pointerleave", () => {
      clearTimeout(horloge);
      video.classList.remove("joue");
      video.pause();
    });
  });
})();

/* --- L'autre porte -------------------------------------------------------
   Sur Colab, le serveur est joignable par deux chemins indépendants : le
   tunnel Cloudflare, et le lien direct de Google. Le premier peut tomber
   sans que rien ne s'arrête derrière — le navigateur affiche alors « Error
   530 » ou « Error 1033 », deux pages anglaises qui ne disent pas quoi faire.

   Tant que l'onglet est ouvert, il reste une issue : la page, elle, est déjà
   chargée. On montre donc l'autre adresse au premier appel qui échoue, en
   gardant le chemin de la page en cours pour revenir au même endroit.

   Et on continue de sonder /ping : un tunnel qui revient tout seul fait
   disparaître le bandeau, sans que l'utilisateur ait eu à toucher à quoi
   que ce soit. */
(() => {
  const bandeau = document.getElementById("porte-secours");
  const lien = document.getElementById("porte-secours-lien");
  const adresse = (document.body.dataset.porteDirecte || "").replace(/\/$/, "");
  if (!bandeau || !lien || !adresse) return;

  // Déjà passé par la porte directe : la proposer serait proposer de rester.
  if (adresse === window.location.origin) return;

  const PERIODE = 5000;
  let horloge = null;

  const cacher = () => {
    bandeau.classList.add("hidden");
    clearInterval(horloge);
    horloge = null;
  };

  const guetter = async () => {
    try {
      const res = await fetch("/ping", { cache: "no-store" });
      if (res.ok) cacher();
    } catch (_) { /* toujours coupé : on reste là */ }
  };

  const montrer = () => {
    if (!bandeau.classList.contains("hidden")) return;
    lien.href = adresse + window.location.pathname + window.location.search;
    bandeau.classList.remove("hidden");
    horloge = setInterval(guetter, PERIODE);
  };

  window.porteDeSecours = { montrer, cacher };
})();
