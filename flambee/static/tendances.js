/* Flambée — Trend Finder : repérer, analyser, s'inspirer. */

const $ = (sel, racine = document) => racine.querySelector(sel);
const $$ = (sel, racine = document) => Array.from(racine.querySelectorAll(sel));

/* Icônes recopiées du gabarit — un seul tracé, en Python, comme app.js. */
function icone(nom) {
  const source = document.querySelector(`#sprites [data-icone="${nom}"]`);
  return source ? source.innerHTML : "";
}

async function api(chemin, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(chemin, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (reseau) {
    throw new Error("Connexion interrompue. Réessaie dans un instant.");
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* réponse vide */ }
  if (!res.ok) {
    throw new Error((data && data.detail) || `Erreur ${res.status}`);
  }
  return data;
}

/* ------------------------------------------------------------- Formats --- */
function formatNombre(n) {
  if (n === null || n === undefined) return null;
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function formatDuree(s) {
  if (s === null || s === undefined) return null;
  const total = Math.round(s);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return m > 0 ? `${m} min ${String(sec).padStart(2, "0")}` : `${sec} s`;
}

function formatDate(iso) {
  if (!iso) return null;
  const d = new Date(iso + "Z");
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

function formatAnciennete(heures) {
  if (heures === null || heures === undefined) return null;
  if (heures < 24) return `il y a ${Math.round(heures)} h`;
  return `il y a ${Math.round(heures / 24)} j`;
}

function echapper(texte) {
  const div = document.createElement("div");
  div.textContent = texte ?? "";
  return div.innerHTML;
}

/* --------------------------------------------------------------- Onglets - */
function activerOnglet(nom) {
  $$(".onglet").forEach((b) => b.classList.toggle("actif", b.dataset.onglet === nom));
  $$(".tf-panneau").forEach((p) => p.classList.toggle("actif", p.dataset.panneau === nom));
  if (nom === "pepites" && !state.pepitesChargees) chargerPepites();
  if (nom === "tendances" && !state.tendancesChargees) chargerTendances();
  if (nom === "mes-tendances") chargerSauvegardes();
  if (nom === "veille") chargerVeille();
}

$$(".onglet").forEach((b) => b.addEventListener("click", () => activerOnglet(b.dataset.onglet)));

/* ----------------------------------------------------------------- État -- */
const state = {
  resultats: [],
  analyses: new Map(),      // url -> dernière analyse reçue (pour "S'inspirer")
  pepitesChargees: false,
  tendancesChargees: false,
  hashtagsFiltre: [],
  derniereRecherche: null,  // pour "Sauvegarder cette recherche"
};

/* ---------------------------------------------------- Formulaire filtres - */
const selectPeriode = $("#tf-periode");
if (selectPeriode) {
  selectPeriode.addEventListener("change", () => {
    const perso = selectPeriode.value === "personnalisee";
    $("#tf-depuis-champ").hidden = !perso;
    $("#tf-jusqu-champ").hidden = !perso;
  });
}

const champHashtag = $("#tf-hashtag-saisie");
const listeHashtags = $("#tf-hashtags-liste");

function rendreHashtagsChoisis() {
  listeHashtags.innerHTML = state.hashtagsFiltre.map((tag, i) => `
    <button type="button" class="tf-tag" data-index="${i}">#${echapper(tag)} ×</button>
  `).join("");
  $$(".tf-tag", listeHashtags).forEach((bouton) => {
    bouton.addEventListener("click", () => {
      state.hashtagsFiltre.splice(Number(bouton.dataset.index), 1);
      rendreHashtagsChoisis();
    });
  });
}

if (champHashtag) {
  champHashtag.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const tag = champHashtag.value.trim().replace(/^#/, "").toLowerCase();
    if (tag && !state.hashtagsFiltre.includes(tag)) {
      state.hashtagsFiltre.push(tag);
      rendreHashtagsChoisis();
    }
    champHashtag.value = "";
  });
}

function afficherAvis(zone, message, type = "erreur") {
  zone.innerHTML = `<div class="avis ${type}">${echapper(message)}</div>`;
}

/* -------------------------------------------------------------- Recherche */
const formulaire = $("#tf-formulaire");
if (formulaire) {
  formulaire.addEventListener("submit", async (e) => {
    e.preventDefault();
    const bouton = $("#tf-bouton-rechercher");
    const donnees = new FormData(formulaire);
    const zoneAvis = $("#tf-resultats-avis");
    zoneAvis.innerHTML = "";

    const periode = donnees.get("periode");
    const corps = {
      liens: donnees.get("liens") || "",
      mots_cles: donnees.get("mots_cles") || "",
      hashtags: state.hashtagsFiltre,
      periode: periode === "personnalisee" ? "" : periode,
      depuis_le: donnees.get("depuis_le_jour") ? `${donnees.get("depuis_le_jour")}T00:00:00` : "",
      jusqu_au: donnees.get("jusqu_au_jour") ? `${donnees.get("jusqu_au_jour")}T23:59:59` : "",
      pays: donnees.get("pays") || "",
      vues_min: donnees.get("vues_min") ? Number(donnees.get("vues_min")) : null,
    };

    bouton.disabled = true;
    bouton.textContent = "Recherche en cours…";
    try {
      const reponse = await api("/api/tendances/recherche", { method: "POST", body: corps });
      state.resultats = reponse.videos;
      state.derniereRecherche = corps;
      (reponse.avertissements || []).forEach((a) => {
        const div = document.createElement("div");
        div.className = "avis";
        div.textContent = a;
        zoneAvis.appendChild(div);
      });
      const echecs = reponse.videos.filter((v) => !v.ok);
      if (echecs.length) {
        const div = document.createElement("div");
        div.className = "avis erreur";
        div.innerHTML = `<b>${echecs.length} lien(s) non récupéré(s)</b>` +
          echecs.map((v) => echapper(`${v.donnees.url} — ${v.erreur}`)).join("<br>");
        zoneAvis.appendChild(div);
      }
      $("#tf-resultats-entete").hidden = reponse.videos.length === 0;
      $("#tf-resultats-compte").textContent =
        `${reponse.total_apres_filtres} vidéo(s) sur ${reponse.total_recu} récupérée(s)`;
      trierEtRendreResultats();
    } catch (err) {
      afficherAvis(zoneAvis, err.message);
    } finally {
      bouton.disabled = false;
      bouton.textContent = "Rechercher";
    }
  });
}

$("#tf-tri")?.addEventListener("change", trierEtRendreResultats);

$("#tf-sauver-recherche")?.addEventListener("click", async (e) => {
  if (!state.derniereRecherche) return;
  const bouton = e.currentTarget;
  const libelle = state.derniereRecherche.mots_cles
    || state.derniereRecherche.hashtags.map((h) => "#" + h).join(" ")
    || `Recherche du ${new Date().toLocaleDateString("fr-FR")}`;
  try {
    await api("/api/tendances/sauvegardes", {
      method: "POST",
      body: { type: "recherche", reference: libelle, libelle,
             donnees: state.derniereRecherche },
    });
    bouton.textContent = "Recherche sauvegardée";
    bouton.disabled = true;
  } catch (err) {
    bouton.textContent = "Échec";
  }
});

function trier(videos, mode) {
  const copie = videos.filter((v) => v.ok).slice();
  const val = (v, champ) => (champ in v.donnees ? v.donnees[champ] : v.analyse[champ]);
  switch (mode) {
    case "recentes":
      copie.sort((a, b) => (b.donnees.publie_le || "").localeCompare(a.donnees.publie_le || ""));
      break;
    case "engagement":
      copie.sort((a, b) => (b.analyse.engagement ?? -1) - (a.analyse.engagement ?? -1));
      break;
    case "emergentes":
      copie.sort((a, b) => (b.analyse.score_pepite ?? -1) - (a.analyse.score_pepite ?? -1));
      break;
    default: // vues
      copie.sort((a, b) => (b.donnees.vues ?? -1) - (a.donnees.vues ?? -1));
  }
  return copie.concat(videos.filter((v) => !v.ok));
}

function trierEtRendreResultats() {
  const mode = $("#tf-tri")?.value || "vues";
  renderCartes($("#tf-resultats-zone"), trier(state.resultats, mode), { pepite: false });
}

/* --------------------------------------------------------------- Cartes -- */
function ligneStat(icon, valeur, suffixe = "") {
  if (valeur === null || valeur === undefined) {
    return `<span class="tf-stat inconnue">${icone(icon)} non communiqué</span>`;
  }
  return `<span class="tf-stat">${icone(icon)} ${formatNombre(valeur)}${suffixe}</span>`;
}

function carteVideo(v, { pepite = false } = {}) {
  const d = v.donnees, a = v.analyse;
  if (!v.ok) return "";
  const miniature = d.miniature
    ? `<img src="${echapper(d.miniature)}" alt="" loading="lazy">`
    : `<div class="tf-vignette-vide">${icone("film")}</div>`;
  const ancienneteTxt = formatAnciennete(a.anciennete_heures);
  const dateTxt = formatDate(d.publie_le);
  const badgePepite = pepite
    ? `<span class="tf-badge-pepite">${icone("eclair")} ${a.mesuree ? "croissance mesurée" : "estimation"}</span>`
    : (a.score_pepite && a.score_pepite > 200
      ? `<span class="tf-badge-pepite">${icone("eclair")} pépite</span>` : "");

  return `
    <article class="carte tf-carte" data-url="${echapper(d.url)}">
      <div class="tf-vignette">${miniature}${badgePepite}</div>
      <div class="tf-corps">
        <p class="tf-titre">${echapper(d.titre || "Sans titre")}</p>
        <p class="tf-createur">${d.createur ? "@" + echapper(d.createur) : "Créateur inconnu"}
          ${dateTxt ? " · " + dateTxt : ""}${ancienneteTxt ? " · " + ancienneteTxt : ""}</p>
        <div class="tf-stats">
          ${ligneStat("oeil", d.vues)}
          ${ligneStat("coeur", d.likes)}
          ${ligneStat("bulle", d.commentaires)}
          ${ligneStat("partage", d.partages)}
          ${d.duree ? `<span class="tf-stat">${icone("horloge")} ${formatDuree(d.duree)}</span>` : ""}
        </div>
        ${pepite ? `<div class="tf-stats">
          <span class="tf-stat">${icone("tendance")}
            ${a.mesuree
              ? `${formatNombre(a.croissance_mesuree_vues_heure)} vues/h mesurées`
              : `~${formatNombre(a.vitesse_vues_heure)} vues/h estimées`}</span>
        </div>` : ""}
        ${d.hashtags && d.hashtags.length ? `<div class="tf-tags">
          ${d.hashtags.slice(0, 6).map((h) => `<span class="tf-tag">#${echapper(h)}</span>`).join("")}
        </div>` : ""}
        <div class="tf-actions">
          <a class="button ghost" href="${echapper(d.url)}" target="_blank" rel="noopener">${icone("lien")} Voir</a>
          <button type="button" class="button ghost tf-btn-analyser">Analyser</button>
          <button type="button" class="button ghost tf-btn-inspirer" disabled
                  title="Lance d'abord Analyser">S'inspirer</button>
          <button type="button" class="button ghost tf-btn-sauvegarder">${icone("marque_page")} Sauvegarder</button>
        </div>
        <div class="tf-panneau-analyse" hidden></div>
      </div>
    </article>`;
}

function renderCartes(zone, videos, options) {
  if (!videos.length) {
    zone.innerHTML = `<div class="tf-vide">${icone("film")}
      <p>Aucun résultat pour l'instant. Colle des liens ci-dessus pour commencer.</p></div>`;
    return;
  }
  zone.innerHTML = videos.map((v) => carteVideo(v, options)).join("");
  $$(".carte.tf-carte", zone).forEach((carte) => brancherCarte(carte, videos));
}

function trouverVideo(videos, url) {
  return videos.find((v) => v.donnees.url === url);
}

function brancherCarte(carte, videos) {
  const url = carte.dataset.url;
  const video = trouverVideo(videos, url);
  const panneau = $(".tf-panneau-analyse", carte);
  const boutonInspirer = $(".tf-btn-inspirer", carte);

  $(".tf-btn-analyser", carte).addEventListener("click", async (e) => {
    const bouton = e.currentTarget;
    bouton.disabled = true;
    bouton.textContent = "Analyse en cours…";
    panneau.hidden = false;
    panneau.innerHTML = `<p class="hint">Téléchargement et analyse du fichier — une
      vingtaine de secondes.</p>`;
    try {
      const analyse = await api("/api/tendances/analyser",
        { method: "POST", body: { url } });
      state.analyses.set(url, analyse.analyse);
      panneau.innerHTML = rendreAnalyse(analyse.analyse);
      boutonInspirer.disabled = false;
      boutonInspirer.title = "";
    } catch (err) {
      panneau.innerHTML = `<div class="avis erreur">${echapper(err.message)}</div>`;
    } finally {
      bouton.disabled = false;
      bouton.textContent = "Ré-analyser";
    }
  });

  boutonInspirer.addEventListener("click", async (e) => {
    const bouton = e.currentTarget;
    const analyse = state.analyses.get(url);
    if (!analyse) return;
    bouton.disabled = true;
    bouton.textContent = "Rédaction…";
    const bloc = document.createElement("div");
    bloc.className = "tf-champ-analyse";
    bloc.innerHTML = "<b>Idée inspirée</b><p>Un instant…</p>";
    panneau.appendChild(bloc);
    try {
      const { idee } = await api("/api/tendances/inspirer",
        { method: "POST", body: { caracteristiques: analyse } });
      bloc.innerHTML = `<b>Idée inspirée — pas une copie</b><p>${echapper(idee)}</p>`;
    } catch (err) {
      bloc.innerHTML = `<div class="avis erreur">${echapper(err.message)}</div>`;
    } finally {
      bouton.disabled = false;
      bouton.textContent = "S'inspirer";
    }
  });

  $(".tf-btn-sauvegarder", carte).addEventListener("click", async (e) => {
    const bouton = e.currentTarget;
    try {
      await api("/api/tendances/sauvegardes", {
        method: "POST",
        body: { type: "video", reference: url, libelle: video?.donnees.titre || url,
               donnees: video || {} },
      });
      bouton.textContent = "Sauvegardé";
      bouton.disabled = true;
    } catch (err) {
      bouton.textContent = "Échec";
    }
  });

  $$(".tf-tag", carte).forEach((tag) => {
    tag.addEventListener("click", async () => {
      const hashtag = tag.textContent.replace("#", "").trim();
      try {
        await api("/api/tendances/sauvegardes", {
          method: "POST",
          body: { type: "hashtag", reference: hashtag, libelle: hashtag },
        });
        tag.style.color = "var(--eclat)";
      } catch (_) { /* silencieux : geste secondaire */ }
    });
  });
}

function rendreAnalyse(a) {
  const champs = [
    ["Sujet", a.sujet],
    ["Amorce (hook)", a.hook_extrait],
    ["Appel à l'action détecté", a.cta_detecte || "Aucun repéré"],
    ["Durée", formatDuree(a.duree_secondes)],
    ["Plans détectés", a.nombre_de_plans],
    ["Rythme", a.rythme_coupes_par_minute ? `~${a.rythme_coupes_par_minute} coupes/min` : null],
    ["Langue", a.langue || null],
  ];
  const structure = a.structure || {};
  const html = champs
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([label, v]) => `<div class="tf-champ-analyse"><b>${label}</b><span>${echapper(String(v))}</span></div>`)
    .join("");
  const structureHtml = (structure.ouverture || structure.developpement || structure.chute)
    ? `<div class="tf-champ-analyse"><b>Découpage par tiers du texte</b>
        <p><em>Ouverture —</em> ${echapper(structure.ouverture || "—")}<br>
           <em>Développement —</em> ${echapper(structure.developpement || "—")}<br>
           <em>Chute —</em> ${echapper(structure.chute || "—")}</p></div>` : "";
  const transcriptionHtml = a.transcription
    ? `<div class="tf-champ-analyse"><b>Transcription</b><p>${echapper(a.transcription)}</p></div>` : "";
  const limitesHtml = (a.limites && a.limites.length)
    ? `<ul class="tf-limites">${a.limites.map((l) => `<li>${echapper(l)}</li>`).join("")}</ul>` : "";
  return html + structureHtml + transcriptionHtml + limitesHtml;
}

/* --------------------------------------------------------------- Pépites - */
async function chargerPepites() {
  const zone = $("#tf-pepites-zone");
  zone.innerHTML = `<div class="tf-chargement">Chargement…</div>`;
  try {
    const { pepites } = await api("/api/tendances/pepites");
    state.pepitesChargees = true;
    if (!pepites.length) {
      zone.innerHTML = `<div class="tf-vide">${icone("eclair")}
        <p>Pas encore assez de recherches pour repérer une pépite. Lance des
        recherches dans l'onglet Résultats : chaque passage enregistre un
        relevé, et une deuxième visite de la même vidéo permet de mesurer une
        vraie croissance.</p></div>`;
      return;
    }
    renderCartes(zone, pepites, { pepite: true });
  } catch (err) {
    afficherAvis(zone, err.message);
  }
}

/* ------------------------------------------------------------- Tendances - */
async function chargerTendances() {
  const zone = $("#tf-tendances-zone");
  zone.innerHTML = `<div class="tf-chargement">Chargement…</div>`;
  try {
    const { hashtags } = await api("/api/tendances/hashtags");
    state.tendancesChargees = true;
    if (!hashtags.length) {
      zone.innerHTML = `<div class="tf-vide">${icone("tendance")}
        <p>Aucun hashtag observé pour l'instant : ils apparaissent au fil de
        tes recherches dans l'onglet Résultats.</p></div>`;
      return;
    }
    zone.innerHTML = hashtags.map((h) => `
      <div class="carte tf-item-simple">
        <div class="tf-item-simple-corps">
          <p class="tf-item-simple-titre">#${echapper(h.hashtag)}</p>
          <p class="tf-item-simple-meta">
            ${h.analyse.nombre_de_videos_observees} vidéo(s) observée(s)
            ${h.analyse.vues_moyennes_observees !== null
              ? ` · ${formatNombre(h.analyse.vues_moyennes_observees)} vues en moyenne` : ""}
          </p>
        </div>
        <button type="button" class="button ghost tf-btn-sauver-hashtag"
                data-hashtag="${echapper(h.hashtag)}">${icone("marque_page")} Sauvegarder</button>
      </div>`).join("");
    $$(".tf-btn-sauver-hashtag", zone).forEach((bouton) => {
      bouton.addEventListener("click", async () => {
        const tag = bouton.dataset.hashtag;
        await api("/api/tendances/sauvegardes",
          { method: "POST", body: { type: "hashtag", reference: tag, libelle: tag } });
        bouton.textContent = "Sauvegardé";
        bouton.disabled = true;
      });
    });
  } catch (err) {
    afficherAvis(zone, err.message);
  }
}

/* ----------------------------------------------------------- Sauvegardes - */
async function chargerSauvegardes() {
  const zone = $("#tf-sauvegardes-zone");
  zone.innerHTML = `<div class="tf-chargement">Chargement…</div>`;
  try {
    const { sauvegardes } = await api("/api/tendances/sauvegardes");
    if (!sauvegardes.length) {
      zone.innerHTML = `<div class="tf-vide">${icone("marque_page")}
        <p>Rien de sauvegardé pour l'instant. Le bouton « Sauvegarder » sur une
        vidéo, un hashtag ou une recherche l'ajoute ici.</p></div>`;
      return;
    }
    zone.innerHTML = sauvegardes.map((s) => `
      <div class="carte tf-item-simple" data-id="${s.id}">
        <div class="tf-item-simple-corps">
          <p class="tf-item-simple-titre">[${s.type}] ${echapper(s.libelle || s.reference)}</p>
          <p class="tf-item-simple-meta">Sauvegardé le ${formatDate(s.cree_le) || s.cree_le}</p>
        </div>
        <button type="button" class="button ghost tf-btn-suppr-sauvegarde">Retirer</button>
      </div>`).join("");
    $$(".tf-btn-suppr-sauvegarde", zone).forEach((bouton) => {
      bouton.addEventListener("click", async () => {
        const carte = bouton.closest(".tf-item-simple");
        await api(`/api/tendances/sauvegardes/${carte.dataset.id}`, { method: "DELETE" });
        carte.remove();
      });
    });
  } catch (err) {
    afficherAvis(zone, err.message);
  }
}

/* ----------------------------------------------------------------- Veille */
async function chargerVeille() {
  const zone = $("#tf-veille-zone");
  zone.innerHTML = `<div class="tf-chargement">Chargement…</div>`;
  try {
    const { surveillances } = await api("/api/tendances/veille");
    if (!surveillances.length) {
      zone.innerHTML = `<div class="tf-vide">${icone("cloche")}
        <p>Aucune veille créée.</p></div>`;
      return;
    }
    zone.innerHTML = surveillances.map((s) => `
      <div class="carte tf-item-simple" data-id="${s.id}">
        <div class="tf-item-simple-corps">
          <p class="tf-item-simple-titre">${echapper(s.libelle)}</p>
          <p class="tf-item-simple-meta">
            ${(s.requete.liens || []).length} lien(s) ·
            ${s.derniere_execution
              ? `dernier relevé le ${formatDate(s.derniere_execution)}`
              : "jamais encore relevée — aucune veille automatique n'est branchée pour l'instant"}
          </p>
        </div>
        <button type="button" class="button ghost tf-btn-suppr-veille">Retirer</button>
      </div>`).join("");
    $$(".tf-btn-suppr-veille", zone).forEach((bouton) => {
      bouton.addEventListener("click", async () => {
        const carte = bouton.closest(".tf-item-simple");
        await api(`/api/tendances/veille/${carte.dataset.id}`, { method: "DELETE" });
        carte.remove();
      });
    });
  } catch (err) {
    afficherAvis(zone, err.message);
  }
}

const formulaireVeille = $("#tf-veille-formulaire");
if (formulaireVeille) {
  formulaireVeille.addEventListener("submit", async (e) => {
    e.preventDefault();
    const donnees = new FormData(formulaireVeille);
    const bouton = $("button[type=submit]", formulaireVeille);
    bouton.disabled = true;
    try {
      await api("/api/tendances/veille", {
        method: "POST",
        body: {
          libelle: donnees.get("libelle") || "",
          liens: donnees.get("liens") || "",
          frequence_heures: Number(donnees.get("frequence_heures")) || 24,
        },
      });
      formulaireVeille.reset();
      chargerVeille();
    } catch (err) {
      afficherAvis($("#tf-veille-zone"), err.message);
    } finally {
      bouton.disabled = false;
    }
  });
}
