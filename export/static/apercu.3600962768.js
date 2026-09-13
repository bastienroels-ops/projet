/* Aperçu des sous-titres dessiné par le navigateur.
   =========================================================================

   Publié en pages statiques, le site n'a pas ffmpeg sous la main : rendre la
   phrase d'un visiteur demanderait un serveur. Le navigateur dessine donc le
   texte lui-même, dans un canevas posé sur le fond — lequel reste, lui, une
   vraie vidéo sortie du moteur.

   Les réglages viennent des mêmes styles que ffmpeg (taille, contour, ombre,
   couleurs, coupure des lignes), lus dans la page. Les dimensions restent
   exprimées dans le cadre de 1080 × 1920 et sont mises à l'échelle ici, comme
   ffmpeg le fait pour la vidéo : deux jeux de valeurs divergeraient.

   Ce n'est pas le rendu final — un canevas n'est pas libass — mais c'est la
   même typographie, les mêmes couleurs et le même découpage. La légende sous
   le cadre le dit. */
(function () {
  "use strict";

  const bloc = document.getElementById("essayage");
  if (!bloc || bloc.dataset.apercu !== "1") return;

  const champ = document.getElementById("essayage-texte");
  const compteur = document.getElementById("essayage-compteur");
  const scene = document.getElementById("essayage-scene");
  const video = document.getElementById("essayage-video");
  const legende = document.getElementById("essayage-legende");
  const puces = [...bloc.querySelectorAll(".puce")];
  if (!scene || !video) return;

  const lire = (id) => {
    try {
      return JSON.parse(document.getElementById(id)?.textContent || "{}");
    } catch (e) {
      return {};
    }
  };
  const STYLES = lire("styles-rendu");
  const DESCRIPTIONS = lire("styles-json");
  if (!Object.keys(STYLES).length) return;

  const CADRE_L = 1080;              // le cadre de référence, celui de la vidéo
  const DUREE_MOT = 0.34;            // même cadence que les échantillons rendus
  const QUEUE = 0.6;                 // temps mort avant que la boucle reprenne
  const MAX = parseInt(champ?.getAttribute("maxlength") || "70", 10);

  /* --- Les polices ------------------------------------------------------
     Celles de ffmpeg, chargées ici aussi : sans elles le navigateur
     retomberait sur une police système et l'aperçu ne ressemblerait plus à
     rien de ce que produit le moteur. */
  const base = bloc.dataset.polices || "/fonts";
  const chargees = new Set();
  function charger(style) {
    if (!style.fichier || chargees.has(style.fichier)) return Promise.resolve();
    chargees.add(style.fichier);
    const police = new FontFace(style.police, `url(${base}/${style.fichier})`);
    return police.load().then((p) => document.fonts.add(p)).catch(() => {});
  }

  /* --- Découpage en lignes ---------------------------------------------
     Reprend la règle du moteur : on ferme la ligne quand elle dépasse le
     nombre de caractères ou de mots du style, et à chaque fin de phrase. */
  const FIN_DE_PHRASE = /[.!?…]$/;

  function decouper(texte, style) {
    const lignes = [];
    let courante = [];
    let longueur = 0;
    for (const brut of texte.split(/\s+/)) {
      const mot = brut.trim();
      if (!mot) continue;
      const ajout = mot.length + (courante.length ? 1 : 0);
      const trop = courante.length
        && (longueur + ajout > style.car_par_ligne
            || courante.length >= style.mots_par_ligne);
      if (trop) {
        lignes.push(courante);
        courante = [];
        longueur = 0;
      }
      courante.push(mot);
      longueur += courante.length === 1 ? mot.length : ajout;
      if (FIN_DE_PHRASE.test(mot)) {
        lignes.push(courante);
        courante = [];
        longueur = 0;
      }
    }
    if (courante.length) lignes.push(courante);
    return lignes;
  }

  /* Minutage : chaque mot dure autant, comme dans les échantillons. On note
     pour chaque ligne le moment où elle paraît et celui où elle s'efface. */
  function minuter(lignes) {
    let index = 0;
    return lignes.map((mots) => {
      const debut = index * DUREE_MOT;
      index += mots.length;
      return { mots, debut, fin: index * DUREE_MOT };
    });
  }

  /* --- Le canevas ------------------------------------------------------- */
  const toile = document.createElement("canvas");
  toile.className = "essayage-toile";
  toile.setAttribute("aria-hidden", "true");
  video.insertAdjacentElement("afterend", toile);
  const ctx = toile.getContext("2d");

  let style = STYLES[puces[0]?.dataset.style] ? puces[0].dataset.style : "punch";
  let lignes = [];
  let duree = 1;
  let echelle = 1;

  function dimensionner() {
    const r = video.getBoundingClientRect();
    if (!r.width) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    toile.width = Math.round(r.width * dpr);
    toile.height = Math.round(r.height * dpr);
    toile.style.width = r.width + "px";
    toile.style.height = r.height + "px";
    // Une seule échelle : du cadre de 1080 aux pixels réellement peints.
    echelle = (r.width / CADRE_L) * dpr;
  }

  /* Le compte de caractères ne suffit pas : « Personne ne t'a jamais » tient
     dans les 22 caractères du style Punch et déborde pourtant du cadre à 92
     pixels de haut. libass replie sur la largeur de l'image ; on mesure donc
     aussi, et on scinde au dernier mot qui entre. */
  function replier(lignes, s) {
    const taille = tailleCss(s);
    const max = toile.width * 0.92;
    const sortie = [];
    for (const mots of lignes) {
      let courante = [];
      for (const mot of mots) {
        const essai = courante.concat(mot);
        if (courante.length && largeurTexte(essai, s, taille) > max) {
          sortie.push(courante);
          courante = [mot];
        } else {
          courante = essai;
        }
      }
      if (courante.length) sortie.push(courante);
    }
    return sortie;
  }

  function recomposer() {
    const s = STYLES[style];
    let texte = (champ?.value || "").trim();
    if (s.capitales) texte = texte.toLocaleUpperCase("fr");
    lignes = minuter(replier(decouper(texte, s), s));
    duree = (lignes.length ? lignes[lignes.length - 1].fin : 0) + QUEUE;
    charger(s);
    if (legende && DESCRIPTIONS[style]) legende.textContent = DESCRIPTIONS[style];
  }

  /* libass traite `Fontsize` comme la hauteur ascendante + descendante de la
     police, non comme son cadratin : le rapport entre les deux est mesuré
     dans le fichier de police et transmis avec le style. Sans lui l'aperçu
     dessine un tiers trop gros. */
  const tailleCss = (s) => s.taille * (s.echelle || 0.75) * echelle;

  function largeurTexte(mots, s, taille) {
    ctx.font = `${taille}px "${s.police}", sans-serif`;
    if ("letterSpacing" in ctx) ctx.letterSpacing = `${s.interlettre * echelle}px`;
    return mots.reduce((total, mot, i) => {
      const espace = i ? ctx.measureText(" ").width : 0;
      return total + espace + ctx.measureText(mot).width;
    }, 0);
  }

  function peindre(t) {
    ctx.clearRect(0, 0, toile.width, toile.height);
    const s = STYLES[style];
    if (!lignes.length) return;

    const ligne = lignes.find((l) => t >= l.debut && t < l.fin)
      || (t >= duree - QUEUE ? lignes[lignes.length - 1] : null);
    if (!ligne) return;

    const taille = tailleCss(s);
    // Le contour, lui, est exprimé en pixels de l'image : il ne subit pas le
    // rapport de la police, seulement la mise à l'échelle de l'affichage.
    const contour = s.contour * echelle;
    const actif = Math.min(
      ligne.mots.length - 1,
      Math.floor((t - ligne.debut) / DUREE_MOT));

    // L'effet « pop » du moteur : la ligne grandit d'un rien en paraissant.
    const age = (t - ligne.debut) / 0.18;
    const pop = age < 1 ? 0.92 + 0.08 * (age * (2 - age)) : 1;

    // La bande montrée est un recadrage autour du texte : celui-ci s'y pose au
    // milieu, exactement comme dans les six échantillons rendus par ffmpeg.
    const milieu = toile.height / 2;

    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if ("letterSpacing" in ctx) ctx.letterSpacing = `${s.interlettre * echelle}px`;
    ctx.translate(toile.width / 2, milieu);
    ctx.scale(pop, pop);

    const largeur = largeurTexte(ligne.mots, s, taille);

    // Le bandeau plein du style « Studio » : dessiné avant le texte.
    if (s.bandeau) {
      const marge = contour;
      ctx.fillStyle = s.couleurs.fond;
      ctx.fillRect(-largeur / 2 - marge, -taille * 0.72,
                   largeur + marge * 2, taille * 1.44);
    }

    ctx.font = `${taille}px "${s.police}", sans-serif`;
    ctx.lineJoin = "round";
    ctx.miterLimit = 2;

    let x = -largeur / 2;
    ligne.mots.forEach((mot, i) => {
      const l = ctx.measureText(mot).width;
      const cx = x + l / 2;
      const estActif = i === actif;
      const grossi = estActif ? s.grossissement : 1;

      ctx.save();
      ctx.translate(cx, 0);
      ctx.scale(grossi, grossi);

      if (!s.bandeau && contour > 0) {
        ctx.strokeStyle = s.couleurs.contour;
        ctx.lineWidth = contour * 2;
        if (s.halo && estActif) {
          ctx.shadowColor = s.couleurs.actif;
          ctx.shadowBlur = s.halo * 2.5 * echelle;
        }
        ctx.strokeText(mot, 0, 0);
        ctx.shadowBlur = 0;
      }
      if (s.ombre) {
        ctx.shadowColor = "rgba(0,0,0,.55)";
        ctx.shadowOffsetX = s.ombre * echelle;
        ctx.shadowOffsetY = s.ombre * echelle;
      }
      ctx.fillStyle = estActif ? s.couleurs.actif : s.couleurs.texte;
      ctx.fillText(mot, 0, 0);
      ctx.restore();

      x += l + ctx.measureText(" ").width;
    });
    ctx.restore();
  }

  /* --- La boucle -------------------------------------------------------- */
  let depart = performance.now();
  function image(maintenant) {
    const t = ((maintenant - depart) / 1000) % duree;
    peindre(t);
    requestAnimationFrame(image);
  }

  /* --- Branchements ----------------------------------------------------- */
  puces.forEach((puce) => {
    puce.addEventListener("click", () => {
      puces.forEach((p) => {
        p.classList.toggle("actif", p === puce);
        p.setAttribute("aria-selected", p === puce ? "true" : "false");
      });
      style = puce.dataset.style;
      recomposer();
      depart = performance.now();          // la phrase repart du début
    });
  });

  if (champ) {
    const majCompteur = () => {
      if (!compteur) return;
      compteur.textContent = `${champ.value.length}/${MAX}`;
      compteur.classList.toggle("proche", champ.value.length > MAX - 10);
    };
    champ.addEventListener("input", () => {
      majCompteur();
      recomposer();
      depart = performance.now();
    });
    majCompteur();
  }

  window.addEventListener("resize", () => {
    dimensionner();
    recomposer();
  }, { passive: true });
  video.addEventListener("loadedmetadata", dimensionner);

  // Les polices d'abord : mesurer un texte avant leur arrivée donnerait des
  // largeurs fausses, et la première image serait mal centrée.
  Promise.all(Object.values(STYLES).map(charger)).then(() => {
    dimensionner();
    recomposer();
    requestAnimationFrame(image);
  });
})();
