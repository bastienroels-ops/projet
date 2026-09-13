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
