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
