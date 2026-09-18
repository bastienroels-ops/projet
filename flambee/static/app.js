/* Flambée — pilotage des 5 étapes depuis une page unique. */

const state = {
  project: null,
  step: 1,
  poll: null,
  presets: [],
  voices: [],
  tracks: [],
  fonds: [],
  filigrane: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ---------------------------------------------------------------- API --- */
async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* réponse vide */ }
  if (!res.ok) throw new Error((data && data.detail) || `Erreur ${res.status}`);
  return data;
}

function alertBox(message, ok = false) {
  const el = $("#alert");
  if (!message) { el.classList.add("hidden"); return; }
  el.textContent = message;
  el.classList.toggle("ok", ok);
  el.classList.remove("hidden");
}

/* ------------------------------------------------------------ Étapes --- */
const NOMS_ETAPES = ["Sources", "Accroche", "Style", "Script", "Rendu"];

function showStep(step) {
  state.step = step;
  $$(".panel").forEach((p) => p.classList.toggle("hidden", +p.dataset.panel !== step));
  $$(".step").forEach((b) => {
    const n = +b.dataset.step;
    b.classList.toggle("active", n === step);
    b.classList.toggle("done", state.project ? n < state.project.step : false);
    b.disabled = state.project ? n > state.project.step : n > 1;
  });

  /* Les cinq pastilles disent où l'on est ; elles ne disent pas combien il
     reste. Une phrase et une barre s'en chargent. */
  const n = $("#parcours-n");
  if (!n) return;
  n.textContent = step;
  $("#parcours-nom").textContent = NOMS_ETAPES[step - 1] || "";
  $("#parcours-part").style.width = `${(step / NOMS_ETAPES.length) * 100}%`;
  const reste = NOMS_ETAPES.length - step;
  $("#parcours-reste").textContent = reste
    ? `Encore ${reste} étape${reste > 1 ? "s" : ""}`
    : "Dernière étape";
}

/* ---------------------------------------------------------- Rendu UI --- */
function renderJob(job) {
  const box = $("#job");
  if (!job || job.state === "idle") { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  $("#btn-cancel").classList.toggle("hidden", job.state !== "running");
  $("#job-fill").style.width = `${Math.round((job.progress || 0) * 100)}%`;
  $("#job-message").textContent = job.error
    ? `${job.message} ${job.error}`
    : job.message || "";
  if (job.state === "error") alertBox(job.error || job.message);
  if (job.state === "done" && job.name === "download") alertBox("", true);
}

function fmtDuration(sec) {
  if (!sec) return "—";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return m ? `${m} min ${String(s).padStart(2, "0")}` : `${s} s`;
}

function renderSources(sources) {
  const box = $("#sources-list");
  box.innerHTML = "";
  sources.forEach((s) => {
    const el = document.createElement("div");
    el.className = "source";
    const meta = [
      s.extractor,
      s.duration ? fmtDuration(s.duration) : null,
      s.width && s.height ? `${s.width}×${s.height}` : null,
      s.view_count ? `${s.view_count.toLocaleString("fr-FR")} vues` : null,
    ].filter(Boolean).join(" · ");
    el.innerHTML = `
      <div class="title">${s.ok ? "✅" : "❌"} ${escapeHtml(s.title || s.url)}</div>
      <div class="meta">${escapeHtml(meta || s.url)}</div>
      ${(s.warnings || []).map((w) => `<div class="warn">⚠️ ${escapeHtml(w)}</div>`).join("")}
      ${s.error ? `<div class="err">${escapeHtml(s.error)}</div>` : ""}`;
    box.appendChild(el);
  });
}

function renderHooks(project) {
  const box = $("#hooks");
  box.innerHTML = "";
  const hooks = project.sources
    .filter((s) => s.ok && s.hook_url)
    .sort((a, b) => (b.hook_score || 0) - (a.hook_score || 0));

  hooks.forEach((s) => {
    const recommended = project.recommended_hook === s.index;
    const el = document.createElement("div");
    el.className = "hook"
      + (project.hook_index === s.index ? " selected" : "")
      + (recommended ? " recommended" : "");
    el.innerHTML = `
      ${recommended ? '<span class="badge">Recommandé</span>' : ""}
      ${s.hook_score ? `<span class="score">${Math.round(s.hook_score * 100)}</span>` : ""}
      <video src="${s.hook_url}#t=0.1" muted loop playsinline preload="metadata"></video>
      <div class="label"><b>${escapeHtml(s.title || "Source " + s.index)}</b>
        ${s.view_count ? s.view_count.toLocaleString("fr-FR") + " vues" : fmtDuration(s.duration)}</div>`;
    const video = el.querySelector("video");
    el.addEventListener("mouseenter", () => video.play().catch(() => {}));
    el.addEventListener("mouseleave", () => { video.pause(); video.currentTime = 0; });
    el.addEventListener("click", () => {
      $$(".hook").forEach((h) => h.classList.remove("selected"));
      el.classList.add("selected");
      state.selectedHook = s.index;
      $("#btn-hook-next").disabled = false;
      video.muted = false;
      video.play().catch(() => {});
    });
    box.appendChild(el);
  });

  if (project.hook_index != null) {
    state.selectedHook = project.hook_index;
    $("#btn-hook-next").disabled = false;
  } else if (project.recommended_hook != null) {
    // Pré-sélection de l'accroche la plus percutante : un clic suffit à valider.
    state.selectedHook = project.recommended_hook;
    $("#btn-hook-next").disabled = false;
  }
}

function renderSettings(settings) {
  $("#voice").value = settings.voice;
  $("#voice_rate").value = settings.voice_rate;
  $("#music").value = settings.music || "";
  $("#music_volume").value = Math.round(settings.music_volume * 100);
  $("#music_volume_v").textContent = `${Math.round(settings.music_volume * 100)}%`;
  $("#subtitles").checked = settings.subtitles;
  $("#split_ratio").value = Math.round(settings.split_ratio * 100);
  $("#split_ratio_v").textContent = `${Math.round(settings.split_ratio * 100)}%`;
  $("#split_bottom").value = settings.split_bottom ? "1" : "0";
  state.splitClip = settings.split_clip || "";
  $("#mask_source_subtitles").checked = settings.mask_source_subtitles;
  $("#keep_source_audio").checked = settings.keep_source_audio;
  $("#mask_mode").value = settings.mask_mode;
  $("#motion").checked = settings.motion;
  $("#scene_aware").checked = settings.scene_aware;
  if ($("#subtitle_preset").options.length) {
    $("#subtitle_preset").value = settings.subtitle_preset;
  }
  $("#mask_height_ratio").value = Math.round(settings.mask_height_ratio * 100);
  $("#mask_height_ratio_v").textContent = `${Math.round(settings.mask_height_ratio * 100)}%`;
  $("#mask-options").classList.toggle("hidden", !settings.mask_source_subtitles);
  $$('input[type="range"]').forEach(paintRange);
  syncCartes();
  describePreset();
}

/* Les réglages viennent du serveur : les cartes doivent s'aligner dessus,
   pas l'inverse. */
function syncCartes() {
  const boites = {
    "#choix-voix": $("#voice").value,
    "#choix-musique": $("#music").value || "",
    "#choix-soustitres": $("#subtitle_preset").value,
  };
  Object.entries(boites).forEach(([sel, valeur]) => {
    const boite = $(sel);
    if (boite) marquerChoisie(boite, valeur);
  });
  const sans = $("#carte-sans-soustitres");
  if (sans) {
    sans.classList.toggle("choisie", !$("#subtitles").checked);
    sans.setAttribute("aria-pressed", String(!$("#subtitles").checked));
  }
  const styles = $("#choix-soustitres");
  if (styles) styles.classList.toggle("eteint", !$("#subtitles").checked);
  const apercu = $("#preset-preview");
  if (apercu) apercu.classList.toggle("eteint", !$("#subtitles").checked);

  /* Les cartes d'écran scindé se contentent d'être remarquées : cette
     fonction tourne à chaque sondage pendant un rendu, et reconstruire la
     grille deux fois par seconde ferait clignoter le panneau. */
  const scinde = $("#choix-scinde");
  if (scinde) marquerChoisie(scinde, state.splitClip || "");
  const reglages = $("#reglages-scinde");
  if (reglages) reglages.hidden = !state.splitClip;
  const couture = $("#note-scinde");
  if (couture) {
    couture.hidden = !state.splitClip || $("#split_bottom").value !== "1";
  }
  resumerReglages();
}

/* Le récapitulatif : six cartes plutôt qu'un tableau. Avant de dépenser un
   crédit, ce qui part au rendu doit se lire d'un coup d'œil. */
function renderRecap(project) {
  const hook = project.sources.find((s) => s.index === project.hook_index);
  const voix = (state.voices || []).find((v) => v.id === $("#voice").value);
  const style = (state.presets || []).find((p) => p.id === $("#subtitle_preset").value);
  const piste = (state.tracks || []).find((t) => t.id === $("#music").value);

  const poser = (id, valeur) => {
    const el = $(id);
    if (el) el.textContent = String(valeur);
  };
  poser("#recap-sources", `${project.sources.filter((s) => s.ok).length} vidéo(s)`);
  poser("#recap-accroche", hook ? (hook.title || "Source " + hook.index) : "—");
  poser("#recap-voix", voix
    ? `${voix.prenom || voix.label}${voix.pays_long ? " · " + voix.pays_long : ""}`
    : project.settings.voice);
  poser("#recap-soustitres", project.settings.subtitles
    ? (style ? style.label : "Animés") : "Désactivés");
  poser("#recap-musique", piste
    ? `${piste.label} · ${Math.round(project.settings.music_volume * 100)}%`
    : "Aucune");
  poser("#recap-duree", fmtDuration(project.estimated_duration));

  const mention = $("#recap-filigrane");
  if (mention) mention.hidden = !state.filigrane;
}

function renderResult(project) {
  const box = $("#result");
  const url = project.output_url || project.preview_url;
  if (!url) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");

  // L'aperçu ne s'affiche que tant qu'aucun rendu définitif n'existe.
  const isPreview = !project.output_url;
  const video = $("#result-video");
  // Lecture sur une copie allégée : le 1080p reste réservé au téléchargement,
  // qu'aucune connexion lente ne vient perturber.
  const lecture = project.viewing_url || url;
  const src = `${lecture}?t=${Math.round(project.job.updated_at || 0)}`;
  if (video.getAttribute("src") !== src) video.setAttribute("src", src);
  video.classList.toggle("preview", isPreview);

  $("#result-info").textContent = isPreview
    ? "Aperçu 540p (non exporté)"
    : project.output_name || "";
  $("#result-badge").textContent = isPreview
    ? "Lance le rendu pour obtenir le .mp4 en 1080×1920 dans /output."
    : "Lecture en version allégée. Le téléchargement livre le 1080×1920.";
  $("#result-download").classList.toggle("hidden", isPreview);
  if (!isPreview) $("#result-download").href = `${project.output_url}?download=true`;
}

/* navigator.clipboard n'existe qu'en contexte sécurisé : absent dès qu'on
   ouvre l'app depuis un téléphone via http://192.168.x.x. On retombe alors sur
   la méthode historique, puis sur l'affichage du texte à copier à la main. */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* on tente le repli */ }

  try {
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(helper);
    helper.select();
    helper.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(helper);
    return ok;
  } catch (_) {
    return false;
  }
}

function showPrompt(prompt, copied) {
  $("#prompt-text").value = prompt;
  $("#prompt-box").classList.remove("hidden");
  $("#prompt-status").textContent = copied
    ? "Prompt copié. Colle-le dans une conversation Claude, puis ramène le script ci-dessous."
    : "Sélectionne et copie le texte ci-dessous, puis colle-le dans une conversation Claude.";
  if (!copied) {
    $("#prompt-text").focus();
    $("#prompt-text").select();
  }
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function applyProject(project) {
  state.project = project;
  renderJob(project.job);
  renderSources(project.sources);
  renderHooks(project);
  renderSettings(project.settings);
  renderRecap(project);
  renderResult(project);
  if (project.topic) $("#topic").value = project.topic;
  if (project.instructions) $("#instructions").value = project.instructions;
  if (project.script && !$("#script").value.trim()) $("#script").value = project.script;
  updateScriptMeta(project);
  showStep(state.step);
  localStorage.setItem("flambee.project", project.id);
}

/* La jauge du script : combien de mots, combien de secondes, et si l'on est
   dans la fenêtre que le moteur vise. Les bornes viennent du serveur, posées
   sur l'élément — les recopier ici en ferait deux vérités. */
function updateScriptMeta(project) {
  const words = $("#script").value.trim().split(/\s+/).filter(Boolean).length;
  const seconds = Math.round((words / 170) * 60);
  const notes = (project && project.script_notes) || [];
  $("#script-meta").textContent = words && notes.length ? notes.join(" ") : "";

  const jauge = $("#jauge-script");
  if (!jauge) return;
  const min = +jauge.dataset.min || 60;
  const max = +jauge.dataset.max || 160;
  const bout = Math.round(max * 1.35);        // de la place au-delà du haut

  $("#jauge-bonne").style.left = `${(min / bout) * 100}%`;
  $("#jauge-bonne").style.width = `${((max - min) / bout) * 100}%`;
  $("#jauge-part").style.width = `${Math.min(100, (words / bout) * 100)}%`;

  const etat = !words ? "vide" : words < min ? "court" : words > max ? "long" : "juste";
  jauge.dataset.etat = etat;
  $("#jauge-compte").textContent = words
    ? `${words} mot${words > 1 ? "s" : ""} · visé ${min} à ${max}`
    : `Vide · visé ${min} à ${max} mots`;
  $("#jauge-duree").textContent = `≈ ${seconds} s de voix off`;

  // Un script vide ne passe pas l'étape : le serveur le refuse déjà, autant
  // que le bouton le dise avant le clic.
  const suivant = $("#btn-script-next");
  if (suivant) suivant.disabled = words === 0;
}

/* --------------------------------------------------------- Polling ----- */
/* Le serveur peut devenir injoignable un moment (tunnel qui tombe pendant un
   encodage, Wi-Fi qui saute) alors que le rendu, lui, continue. On ne coupe
   donc pas le suivi à la première erreur : on prévient, et on réessaie. */
function startPolling() {
  stopPolling();
  const startedAt = Date.now();
  let echecs = 0;

  state.poll = setInterval(async () => {
    if (!state.project) return;
    try {
      const project = await api(`/api/projects/${state.project.id}`);
      const wasRunning = state.project.job.state === "running";
      if (echecs) { echecs = 0; alertBox(""); }
      applyProject(project);

      // Une tâche qui n'a pas encore démarré ne doit pas interrompre le suivi.
      if (project.job.state !== "running" && Date.now() - startedAt > 2500) {
        stopPolling();
        if (wasRunning && ["download", "import"].includes(project.job.name)
            && project.job.state === "done") {
          showStep(2);
        }
        if (wasRunning && project.job.name === "render" && project.job.state === "done") {
          showStep(5);
        }
        // La deuxième vidéo vient d'arriver : elle doit apparaître dans la
        // liste des fonds, et s'y trouver déjà choisie.
        if (wasRunning && project.job.name === "fond"
            && project.job.state === "done") {
          $("#fond-lien").value = "";
          loadFonds().catch((e) => console.error(e));
        }
      }
    } catch (err) {
      echecs += 1;
      console.error(err);
      if (echecs === 3) {
        alertBox("Connexion au serveur perdue — le rendu continue de son côté. "
          + "Nouvelle tentative en cours…\nSi ça dure, retourne sur l'onglet "
          + "Colab : une nouvelle adresse y est peut-être affichée.");
      }
      if (echecs > 60) {          // ~2 min sans réponse : on cesse d'insister
        stopPolling();
        alertBox("Serveur injoignable. Reviens sur l'onglet Colab pour "
          + "récupérer la nouvelle adresse, puis recharge cette page.");
      }
    }
  }, 2000);
}

function stopPolling() {
  if (state.poll) { clearInterval(state.poll); state.poll = null; }
}

/* ------------------------------------------------------ Chargement ----- */
async function loadHealth() {
  const h = await api("/api/health");
  const tag = (label, ok) => `<span class="tag ${ok ? "ok" : "ko"}">${label}</span>`;
  applyScriptMode(h.anthropic_key);
  // Le bandeau d'état n'existe que sur l'ancienne page ; l'application le
  // remplace par la rubrique Paramètres.
  const bandeau = $("#health");
  if (bandeau) {
    bandeau.innerHTML =
      tag("ffmpeg", h.ffmpeg) + tag("yt-dlp", h.yt_dlp) +
      tag(h.anthropic_key ? "clé Claude" : "script manuel", true) +
      tag(`${h.music_count} musique(s)`, true) +
      (h.auth ? tag("protégé", true) : "");
  }
  if (!h.ffmpeg || !h.yt_dlp) {
    alertBox("Dépendances manquantes — va voir la rubrique Paramètres.");
  }
}

/* Sans clé API, « Générer avec Claude » ne peut pas aboutir : plutôt que de
   laisser l'utilisateur buter sur un message d'erreur, on désactive le bouton
   et on met le mode manuel en avant. */
function applyScriptMode(hasApiKey) {
  const generate = $("#btn-generate");
  const copy = $("#btn-copy-prompt");
  generate.disabled = !hasApiKey;
  generate.title = hasApiKey ? "" : "Nécessite une clé API Anthropic";
  generate.classList.toggle("primary", hasApiKey);
  generate.classList.toggle("ghost", !hasApiKey);
  copy.classList.toggle("primary", !hasApiKey);
  copy.classList.toggle("ghost", hasApiKey);
  copy.textContent = hasApiKey
    ? "Copier le prompt (mode manuel)"
    : "Écrire le script avec Claude";
  $("#manual-hint").classList.toggle("hidden", hasApiKey);
}

/* ====================================================================== */
/* Les cartes de l'étape Style                                             */
/*                                                                         */
/* Une voix ne se choisit pas sur son nom dans une liste déroulante, ni une */
/* musique sur le sien. Chaque réglage devient une carte : ce qu'on prend,  */
/* ce qu'on n'a pas encore, et de quoi l'entendre. Les listes déroulantes   */
/* restent en place, masquées : tout le reste du script les lit et les      */
/* écrit, et une carte ne fait que les piloter.                            */
/* ====================================================================== */

/* Les icônes viennent du gabarit, qui les tient du jeu d'icônes du serveur.
   Rien n'est redessiné ici : une icône n'a qu'un seul tracé, et il est en
   Python. */
function icone(nom) {
  const source = document.querySelector(`#sprites [data-icone="${nom}"]`);
  return source ? source.innerHTML : "";
}

/* Un seul lecteur pour toute la page : sans cela, deux extraits se
   superposent et l'on n'entend plus ni l'un ni l'autre. */
const audition = { lecteur: null, bouton: null };

function arreterAudition() {
  if (audition.lecteur) { audition.lecteur.pause(); audition.lecteur = null; }
  if (audition.bouton) {
    audition.bouton.innerHTML = icone("lecture");
    audition.bouton.classList.remove("joue", "charge");
    audition.bouton = null;
  }
}

function ecouter(url, bouton) {
  const enCours = audition.bouton === bouton;
  arreterAudition();
  if (enCours) return;                    // deuxième clic : on arrête

  const lecteur = new Audio(url);
  audition.lecteur = lecteur;
  audition.bouton = bouton;
  bouton.classList.add("charge");
  lecteur.addEventListener("playing", () => {
    bouton.classList.remove("charge");
    bouton.classList.add("joue");
    bouton.innerHTML = icone("pause");
  });
  lecteur.addEventListener("ended", arreterAudition);
  /* La première audition d'une voix passe par une synthèse : elle peut
     échouer (hors ligne, ffmpeg absent). On le dit sur la carte plutôt que
     de laisser un bouton qui ne répond pas. */
  lecteur.addEventListener("error", () => {
    const carte = bouton.closest(".carte-choix");
    arreterAudition();
    if (carte) carte.classList.add("muette");
  });
  lecteur.play().catch(() => {
    const carte = bouton.closest(".carte-choix");
    arreterAudition();
    if (carte) carte.classList.add("muette");
  });
}

/* Fabrique une carte. `option` : vignette, titre, ligne de détail, note,
   url d'écoute, verrou. */
function carteChoix(option) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "carte-choix" + (option.verrouille ? " verrouille" : "");
  el.dataset.valeur = option.valeur;
  el.setAttribute("aria-pressed", "false");
  if (option.verrouille) {
    el.disabled = true;
    el.title = "Cette option fait partie de la formule Créateur.";
  }
  el.innerHTML = `
    ${option.vignette ? `<span class="carte-vignette">${option.vignette}</span>` : ""}
    <span class="carte-mots">
      <b>${escapeHtml(option.titre)}</b>
      ${option.detail ? `<small>${escapeHtml(option.detail)}</small>` : ""}
      ${option.note ? `<em>${escapeHtml(option.note)}</em>` : ""}
      ${option.verrouille
        ? `<span class="carte-verrou">${icone("cadenas")} Créateur</span>` : ""}
    </span>
    ${option.ecoute && !option.verrouille
      ? `<span class="carte-ecoute" role="button" tabindex="-1"
             aria-label="Écouter">${icone("lecture")}</span>` : ""}
    <span class="carte-marque"></span>`;

  if (option.ecoute && !option.verrouille) {
    el.querySelector(".carte-ecoute").addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();               // écouter n'est pas choisir
      ecouter(option.ecoute, e.currentTarget);
    });
  }
  return el;
}

/* Une carte choisie écrit dans la liste déroulante et prévient le reste du
   script par un évènement `change` — exactement comme si l'on avait ouvert
   la liste à la main. */
function choisir(select, valeur) {
  select.value = valeur;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function marquerChoisie(conteneur, valeur) {
  conteneur.querySelectorAll(".carte-choix").forEach((c) => {
    const prise = c.dataset.valeur === valeur;
    c.classList.toggle("choisie", prise);
    c.setAttribute("aria-pressed", String(prise));
  });
}

async function loadVoices() {
  const { voices, default: def } = await api("/api/voices");
  state.voices = voices;
  $("#voice").innerHTML = voices
    .map((v) => `<option value="${v.id}"${v.verrouille ? " disabled" : ""}>`
      + escapeHtml(v.label) + "</option>").join("");
  $("#voice").value = def;

  // Le filtre de pays se remplit de ce que la liste contient réellement.
  const pays = [...new Set(voices.map((v) => v.pays).filter(Boolean))];
  $("#filtre-pays").innerHTML = '<option value="">Tous</option>'
    + pays.map((p) => `<option value="${escapeHtml(p)}">`
      + escapeHtml(state.voices.find((v) => v.pays === p).pays_long || p)
      + "</option>").join("");

  peindreVoix();
}

function peindreVoix() {
  const boite = $("#choix-voix");
  if (!boite) return;
  const parPays = $("#filtre-pays") ? $("#filtre-pays").value : "";
  const parGenre = $("#filtre-genre") ? $("#filtre-genre").value : "";
  const retenues = (state.voices || []).filter((v) =>
    (!parPays || v.pays === parPays) && (!parGenre || v.genre === parGenre));

  boite.innerHTML = "";
  retenues.forEach((v) => {
    const detail = [v.pays_long, v.genre_long].filter(Boolean).join(" · ");
    const carte = carteChoix({
      valeur: v.id,
      vignette: escapeHtml((v.prenom || v.id).slice(0, 2).toUpperCase()),
      titre: v.prenom || v.label,
      detail,
      note: v.note || "",
      ecoute: `/api/voices/${encodeURIComponent(v.id)}/sample`,
      verrouille: v.verrouille,
    });
    carte.addEventListener("click", () => choisir($("#voice"), v.id));
    boite.appendChild(carte);
  });

  const vide = $("#voix-aucune");
  if (vide) vide.hidden = retenues.length > 0;
  marquerChoisie(boite, $("#voice").value);
  resumerReglages();
}

async function loadPresets() {
  const { subtitles, default: def, debit_reglable, filigrane } =
    await api("/api/presets");
  state.presets = subtitles;
  state.filigrane = filigrane;
  /* Les styles réservés restent visibles mais non sélectionnables : les
     masquer rendrait la différence entre formules invisible, et l'on choisit
     mal ce qu'on ne voit pas. */
  $("#subtitle_preset").innerHTML = subtitles
    .map((p) => `<option value="${p.id}"${p.verrouille ? " disabled" : ""}>`
      + escapeHtml(p.label) + (p.verrouille ? " — Créateur" : "")
      + "</option>").join("");
  const ouvert = subtitles.find((p) => !p.verrouille);
  const defautOuvert = subtitles.some((p) => p.id === def && !p.verrouille);
  $("#subtitle_preset").value = defautOuvert ? def : (ouvert ? ouvert.id : def);
  peindreStyles();

  const reserves = subtitles.some((p) => p.verrouille);
  const note = $("#styles-reserves");
  if (note) note.hidden = !reserves;
  const debit = $("#bloc-debit");
  if (debit) debit.hidden = !debit_reglable;
  describePreset();
}

/* Chaque style s'accompagne d'un échantillon vidéo rendu par ffmpeg : ce que
   l'on voit ici est exactement ce que produira le montage. */
function describePreset() {
  const id = $("#subtitle_preset").value;
  const choisi = state.presets.find((p) => p.id === id);
  if (!choisi) return;

  $("#preset-title").textContent = choisi.label;
  $("#preset-description").textContent = choisi.description;

  const video = $("#preset-video");
  const src = `/api/presets/${encodeURIComponent(id)}/sample`;
  if (video.dataset.preset === id) return;      // déjà à l'écran

  video.dataset.preset = id;
  // L'affiche s'affiche pendant le rendu : le cadre ne reste jamais noir.
  video.poster = `/api/presets/${encodeURIComponent(id)}/poster`;
  const cadre = $("#preset-preview");
  cadre.classList.add("loading");
  cadre.classList.remove("unplayable");
  video.hidden = false;
  video.src = src;

  // Le premier rendu d'un style prend une seconde ; ensuite il vient du cache.
  video.oncanplay = () => {
    cadre.classList.remove("loading");
    video.play().catch(() => {});
  };
  // Rendu indisponible (ffmpeg absent) ou navigateur sans décodeur H.264 :
  // on garde le nom et la description plutôt que de tout escamoter.
  video.onerror = () => {
    cadre.classList.remove("loading");
    cadre.classList.add("unplayable");
    video.hidden = true;
  };
}

/* La piste du curseur se remplit jusqu'à la valeur choisie. */
function paintRange(input) {
  const min = +input.min || 0;
  const max = +input.max || 100;
  const part = ((+input.value - min) / (max - min)) * 100;
  input.style.backgroundSize = `${part}% 100%`;
}

function peindreStyles() {
  const boite = $("#choix-soustitres");
  if (!boite) return;
  boite.innerHTML = "";
  (state.presets || []).forEach((p) => {
    const carte = carteChoix({
      valeur: p.id,
      titre: p.label,
      note: p.description || "",
      verrouille: p.verrouille,
    });
    // L'aperçu du style se rend en vidéo : l'affiche sert de vignette, et
    // montre le style tel qu'il sortira du moteur.
    const vignette = document.createElement("span");
    vignette.className = "carte-apercu";
    vignette.style.backgroundImage =
      `url("/api/presets/${encodeURIComponent(p.id)}/poster")`;
    carte.prepend(vignette);
    carte.addEventListener("click", () => {
      if (!$("#subtitles").checked) {      // reprendre un style, c'est les rallumer
        $("#subtitles").checked = true;
        $("#subtitles").dispatchEvent(new Event("change", { bubbles: true }));
      }
      choisir($("#subtitle_preset"), p.id);
    });
    boite.appendChild(carte);
  });
  marquerChoisie(boite, $("#subtitle_preset").value);
  resumerReglages();
}

function fmtMinutes(secondes) {
  if (!secondes) return "";
  const m = Math.floor(secondes / 60), s = Math.round(secondes % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

async function loadMusic() {
  const { tracks, autorisee } = await api("/api/music");
  state.tracks = tracks;
  const choix = $("#music");
  choix.innerHTML = `<option value="">Aucune</option>` +
    tracks.map((t) => `<option value="${escapeHtml(t.id)}"`
      + `${t.verrouille ? " disabled" : ""}>${escapeHtml(t.label)}</option>`).join("");
  // Un curseur de volume sans musique possible n'a rien à régler.
  const volume = $("#bloc-volume");
  if (volume) volume.hidden = !autorisee;
  /* Une liste vide sans explication passe pour une panne : on dit laquelle
     des deux raisons s'applique. */
  const note = $("#note-musique");
  if (note) {
    note.hidden = autorisee && tracks.length > 0;
    note.textContent = !autorisee
      ? "La musique de fond et son mixage sous la parole font partie de la formule Créateur."
      : "Aucune piste pour l'instant : dépose tes fichiers audio dans le dossier des musiques.";
  }
  peindreMusiques();
}

function peindreMusiques() {
  const boite = $("#choix-musique");
  if (!boite) return;
  boite.innerHTML = "";

  // Le refus vient en premier : c'est le choix par défaut, il doit se voir.
  const aucune = carteChoix({
    valeur: "", titre: "Sans musique de fond",
    note: "La voix off seule. Le choix le plus sûr pour un script dense.",
  });
  aucune.classList.add("carte-sans");
  aucune.addEventListener("click", () => choisir($("#music"), ""));
  boite.appendChild(aucune);

  (state.tracks || []).forEach((piste) => {
    const carte = carteChoix({
      valeur: piste.id,
      titre: piste.label,
      detail: fmtMinutes(piste.duree),
      ecoute: `/api/music/${encodeURIComponent(piste.id)}/sample`,
      verrouille: piste.verrouille,
    });
    carte.addEventListener("click", () => choisir($("#music"), piste.id));
    boite.appendChild(carte);
  });
  marquerChoisie(boite, $("#music").value || "");
  resumerReglages();
}

/* Replié, un réglage doit dire ce qu'il vaut : sans ce résumé, refermer un
   panneau reviendrait à cacher son propre choix. */
function resumerReglages() {
  const dire = (id, texte) => { const el = $(id); if (el) el.textContent = texte; };

  const voix = (state.voices || []).find((v) => v.id === $("#voice").value);
  dire("#resume-voix", voix
    ? [voix.prenom || voix.label, [voix.pays_long, voix.genre_long]
        .filter(Boolean).join(", ")].filter(Boolean).join(" · ")
    : "—");

  const style = (state.presets || []).find((p) => p.id === $("#subtitle_preset").value);
  dire("#resume-soustitres", !$("#subtitles").checked ? "Désactivés"
    : (style ? style.label : "—"));

  const piste = (state.tracks || []).find((t) => t.id === $("#music").value);
  dire("#resume-musique", piste
    ? `${piste.label} · ${$("#music_volume").value}%` : "Aucune");

  const fond = (state.fonds || []).find((f) => f.id === state.splitClip);
  dire("#resume-scinde", fond
    ? `${fond.label} · ${$("#split_ratio").value}% pour le montage`
    : "Aucun");

  const options = [
    $("#motion").checked && "travelling",
    $("#scene_aware").checked && "coupes calées",
    $("#mask_source_subtitles").checked && "sous-titres sources masqués",
    $("#keep_source_audio").checked && "ambiance conservée",
  ].filter(Boolean);
  dire("#resume-image", options.length ? options.join(" · ") : "Aucun effet");
}

async function loadFonds() {
  const id = state.project ? state.project.id : "";
  const { fonds } = await api(`/api/fonds?projet=${encodeURIComponent(id)}`);
  state.fonds = fonds;
  peindreFonds();
}

function peindreFonds() {
  const boite = $("#choix-scinde");
  if (!boite) return;
  boite.innerHTML = "";

  const aucun = carteChoix({
    valeur: "", titre: "Sans écran scindé",
    note: "Le montage occupe tout le cadre, comme aujourd'hui.",
  });
  aucun.classList.add("carte-sans");
  aucun.addEventListener("click", () => choisirFond(""));
  boite.appendChild(aucun);

  (state.fonds || []).forEach((fond) => {
    const carte = carteChoix({
      valeur: fond.id,
      titre: fond.label,
      detail: fond.projet ? "Déposée sur ce projet" : fmtMinutes(fond.duree),
      note: fond.projet ? "" : "Bouclée pour couvrir toute la vidéo.",
    });
    // Une image tirée de la boucle : on choisit mal une vidéo sur son nom.
    const vignette = document.createElement("span");
    vignette.className = "carte-apercu";
    vignette.style.backgroundImage =
      `url("/api/fonds/${encodeURIComponent(fond.id)}/poster`
      + `?projet=${encodeURIComponent(state.project ? state.project.id : "")}")`;
    carte.prepend(vignette);
    carte.addEventListener("click", () => choisirFond(fond.id));
    boite.appendChild(carte);
  });

  marquerChoisie(boite, state.splitClip || "");
  const actif = Boolean(state.splitClip);
  $("#reglages-scinde").hidden = !actif;
  $("#note-scinde").hidden = !actif || $("#split_bottom").value !== "1";
  resumerReglages();
}

function choisirFond(valeur) {
  state.splitClip = valeur;
  peindreFonds();
}

async function newProject() {
  const project = await api("/api/projects", { method: "POST" });
  $("#script").value = "";
  applyProject(project);
  showStep(1);
  alertBox("");
}

async function loadOrCreate() {
  const saved = localStorage.getItem("flambee.project");
  if (saved) {
    try {
      const project = await api(`/api/projects/${saved}`);
      applyProject(project);
      showStep(Math.min(project.step, 5));
      if (project.job.state === "running") startPolling();
      return;
    } catch (_) { /* projet disparu : on en crée un neuf */ }
  }
  await newProject();
}

/* ------------------------------------------------------- Actions ------- */
function bind() {
  $("#btn-new").addEventListener("click", newProject);

  $$(".step").forEach((b) => b.addEventListener("click", () => {
    if (!b.disabled) showStep(+b.dataset.step);
  }));

  $("#btn-download").addEventListener("click", async (e) => {
    const urls = $("#urls").value;
    try {
      alertBox("");
      e.target.disabled = true;
      applyProject(await api(`/api/projects/${state.project.id}/sources`,
        { method: "POST", body: { urls } }));
      startPolling();
    } catch (err) { alertBox(err.message); }
    finally { e.target.disabled = false; }
  });

  $("#uploads").addEventListener("change", (e) => {
    const files = Array.from(e.target.files || []);
    const total = files.reduce((sum, f) => sum + f.size, 0);
    $("#upload-list").textContent = files.length
      ? `${files.length} fichier(s) — ${(total / 1e6).toFixed(0)} Mo`
      : "";
    $("#btn-upload").classList.toggle("hidden", files.length === 0);
  });

  $("#btn-upload").addEventListener("click", async (e) => {
    const files = Array.from($("#uploads").files || []);
    if (!files.length) return;
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    try {
      alertBox("");
      e.target.disabled = true;
      e.target.textContent = "Envoi…";
      // L'envoi peut être long depuis un téléphone : pas de JSON, du multipart.
      const res = await fetch(`/api/projects/${state.project.id}/uploads`,
        { method: "POST", body: form });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || `Erreur ${res.status}`);
      applyProject(data);
      startPolling();
    } catch (err) { alertBox(err.message); }
    finally { e.target.disabled = false; e.target.textContent = "Importer"; }
  });

  $("#btn-hook-next").addEventListener("click", async () => {
    try {
      applyProject(await api(`/api/projects/${state.project.id}/hook`,
        { method: "POST", body: { hook_index: state.selectedHook } }));
      showStep(3);
    } catch (err) { alertBox(err.message); }
  });

  const settingsPayload = () => ({
    voice: $("#voice").value,
    voice_rate: $("#voice_rate").value,
    subtitles: $("#subtitles").checked,
    music: $("#music").value || null,
    music_volume: +$("#music_volume").value / 100,
    mask_source_subtitles: $("#mask_source_subtitles").checked,
    mask_mode: $("#mask_mode").value,
    mask_height_ratio: +$("#mask_height_ratio").value / 100,
    keep_source_audio: $("#keep_source_audio").checked,
    motion: $("#motion").checked,
    scene_aware: $("#scene_aware").checked,
    subtitle_preset: $("#subtitle_preset").value,
    split_clip: state.splitClip || "",
    split_ratio: +$("#split_ratio").value / 100,
    split_bottom: $("#split_bottom").value === "1",
  });

  $("#music_volume").addEventListener("input", (e) => {
    $("#music_volume_v").textContent = `${e.target.value}%`;
    paintRange(e.target);
  });
  $("#mask_height_ratio").addEventListener("input", (e) => {
    $("#mask_height_ratio_v").textContent = `${e.target.value}%`;
    paintRange(e.target);
  });
  $("#subtitle_preset").addEventListener("change", () => {
    describePreset();
    syncCartes();
  });
  $("#mask_source_subtitles").addEventListener("change", (e) => {
    $("#mask-options").classList.toggle("hidden", !e.target.checked);
  });

  // Choisir une voix ou une musique doit se voir immédiatement sur la carte.
  $("#voice").addEventListener("change", syncCartes);
  $("#music").addEventListener("change", syncCartes);
  $("#subtitles").addEventListener("change", syncCartes);
  ["#motion", "#scene_aware", "#mask_source_subtitles", "#keep_source_audio"]
    .forEach((sel) => $(sel).addEventListener("change", resumerReglages));
  $("#music_volume").addEventListener("input", resumerReglages);
  $("#split_ratio").addEventListener("input", (e) => {
    $("#split_ratio_v").textContent = `${e.target.value}%`;
    paintRange(e.target);
    resumerReglages();
  });
  $("#split_bottom").addEventListener("change", () => {
    $("#note-scinde").hidden = !state.splitClip || $("#split_bottom").value !== "1";
    resumerReglages();
  });

  $("#btn-fond-lien").addEventListener("click", async (e) => {
    const lien = $("#fond-lien").value.trim();
    if (!lien || !state.project) return;
    try {
      alertBox("");
      e.target.disabled = true;
      applyProject(await api(`/api/projects/${state.project.id}/fond/lien`,
        { method: "POST", body: { url: lien } }));
      startPolling();
    } catch (err) { alertBox(err.message); }
    finally { e.target.disabled = false; }
  });

  $("#fond-fichier").addEventListener("change", async (e) => {
    const fichier = (e.target.files || [])[0];
    if (!fichier || !state.project) return;
    const etat = $("#fond-etat");
    etat.textContent = "Envoi…";
    const form = new FormData();
    form.append("file", fichier, fichier.name);
    try {
      const res = await fetch(`/api/projects/${state.project.id}/fond`,
        { method: "POST", body: form });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || `Erreur ${res.status}`);
      etat.textContent = `${data.largeur}×${data.hauteur}, ${data.duree} s`;
      state.splitClip = "@projet";
      await loadFonds();
    } catch (err) {
      etat.textContent = "";
      alertBox(err.message);
    } finally {
      e.target.value = "";
    }
  });

  // La carte « Sans sous-titres » est une bascule, pas un interrupteur caché.
  const sansSoustitres = $("#carte-sans-soustitres");
  if (sansSoustitres) {
    sansSoustitres.addEventListener("click", () => {
      const boite = $("#subtitles");
      boite.checked = !boite.checked;     // la carte est prise quand ils sont éteints
      boite.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  // Les filtres de voix.
  ["#filtre-pays", "#filtre-genre"].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener("change", peindreVoix);
  });

  /* Les panneaux pliants : un seul réglage ouvert à la fois, pour que l'étape
     tienne dans un écran de téléphone. */
  $$(".reglage-tete").forEach((tete) => {
    tete.addEventListener("click", () => {
      const panneau = tete.closest(".reglage");
      const ouvre = !panneau.classList.contains("ouvert");
      arreterAudition();
      $$(".reglage").forEach((autre) => {
        autre.classList.toggle("ouvert", autre === panneau && ouvre);
        autre.querySelector(".reglage-tete")
          .setAttribute("aria-expanded", String(autre === panneau && ouvre));
      });
    });
  });

  $("#btn-style-next").addEventListener("click", async () => {
    try {
      applyProject(await api(`/api/projects/${state.project.id}/settings`,
        { method: "POST", body: settingsPayload() }));
      showStep(4);
    } catch (err) { alertBox(err.message); }
  });

  const scriptPayload = () => ({
    topic: $("#topic").value,
    instructions: $("#instructions").value,
    duration: +$("#duration").value,
    script: $("#script").value,
  });

  $("#btn-generate").addEventListener("click", async (e) => {
    try {
      alertBox("");
      e.target.disabled = true;
      e.target.textContent = "Génération…";
      const project = await api(`/api/projects/${state.project.id}/script/generate`,
        { method: "POST", body: scriptPayload() });
      $("#script").value = project.script;
      applyProject(project);
    } catch (err) { alertBox(err.message); }
    finally { e.target.disabled = false; e.target.textContent = "Générer avec Claude"; }
  });

  $("#btn-copy-prompt").addEventListener("click", async () => {
    try {
      const { prompt } = await api(`/api/projects/${state.project.id}/script/prompt`,
        { method: "POST", body: scriptPayload() });
      const copied = await copyText(prompt);
      showPrompt(prompt, copied);
    } catch (err) {
      alertBox(err.message);
    }
  });

  $("#btn-prompt-hide").addEventListener("click", () => {
    $("#prompt-box").classList.add("hidden");
  });

  $("#script").addEventListener("input", () => updateScriptMeta(state.project));

  $("#btn-script-next").addEventListener("click", async () => {
    try {
      applyProject(await api(`/api/projects/${state.project.id}/script`,
        { method: "POST", body: scriptPayload() }));
      showStep(5);
    } catch (err) { alertBox(err.message); }
  });

  const startRender = async (button, fast) => {
    try {
      alertBox("");
      button.disabled = true;
      applyProject(await api(`/api/projects/${state.project.id}/render`,
        { method: "POST", body: { fast } }));
      startPolling();
    } catch (err) { alertBox(err.message); }
    finally { button.disabled = false; }
  };

  $("#btn-render").addEventListener("click", (e) => startRender(e.target, false));
  $("#btn-preview").addEventListener("click", (e) => startRender(e.target, true));

  $("#btn-cancel").addEventListener("click", async (e) => {
    try {
      e.target.disabled = true;
      await api(`/api/projects/${state.project.id}/cancel`, { method: "POST" });
      alertBox("Arrêt demandé…", true);
    } catch (err) { alertBox(err.message); }
    finally { e.target.disabled = false; }
  });

  $("#btn-cleanup").addEventListener("click", async () => {
    try {
      const { freed_bytes } = await api(`/api/projects/${state.project.id}/cleanup`,
        { method: "POST" });
      alertBox(`${(freed_bytes / 1e6).toFixed(1)} Mo libérés.`, true);
    } catch (err) { alertBox(err.message); }
  });
}

bind();
loadHealth().catch((e) => console.error(e));
Promise.all([loadVoices(), loadMusic(), loadPresets()])
  .then(loadOrCreate)
  // Les fonds dépendent du projet : la liste inclut la vidéo qu'on y a déposée.
  .then(() => loadFonds().catch((e) => console.error(e)))
  .catch((err) => alertBox(err.message));
