/* Flambée — pilotage des 5 étapes depuis une page unique. */

const state = {
  project: null,
  step: 1,
  poll: null,
  presets: [],
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
function showStep(step) {
  state.step = step;
  $$(".panel").forEach((p) => p.classList.toggle("hidden", +p.dataset.panel !== step));
  $$(".step").forEach((b) => {
    const n = +b.dataset.step;
    b.classList.toggle("active", n === step);
    b.classList.toggle("done", state.project ? n < state.project.step : false);
    b.disabled = state.project ? n > state.project.step : n > 1;
  });
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
  describePreset();
}

function renderRecap(project) {
  const hook = project.sources.find((s) => s.index === project.hook_index);
  const music = $("#music").selectedOptions[0];
  $("#recap").innerHTML = `<dl>
    <dt>Sources</dt><dd>${project.sources.filter((s) => s.ok).length} vidéo(s)</dd>
    <dt>Accroche</dt><dd>${escapeHtml(hook ? (hook.title || "Source " + hook.index) : "—")}</dd>
    <dt>Voix</dt><dd>${escapeHtml($("#voice").selectedOptions[0]?.textContent || project.settings.voice)}</dd>
    <dt>Sous-titres</dt><dd>${project.settings.subtitles ? "Animés" : "Désactivés"}</dd>
    <dt>Musique</dt><dd>${escapeHtml(music && music.value ? music.textContent : "Aucune")}</dd>
    <dt>Durée estimée</dt><dd>${fmtDuration(project.estimated_duration)}</dd>
  </dl>`;
}

function renderResult(project) {
  const box = $("#result");
  const url = project.output_url || project.preview_url;
  if (!url) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");

  // L'aperçu ne s'affiche que tant qu'aucun rendu définitif n'existe.
  const isPreview = !project.output_url;
  const video = $("#result-video");
  const src = `${url}?t=${Math.round(project.job.updated_at || 0)}`;
  if (video.getAttribute("src") !== src) video.setAttribute("src", src);
  video.classList.toggle("preview", isPreview);

  $("#result-info").textContent = isPreview
    ? "Aperçu 540p (non exporté)"
    : project.output_name || "";
  $("#result-badge").textContent = isPreview
    ? "Lance le rendu pour obtenir le .mp4 en 1080×1920 dans /output."
    : "";
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

function updateScriptMeta(project) {
  const words = $("#script").value.trim().split(/\s+/).filter(Boolean).length;
  const seconds = Math.round((words / 170) * 60);
  const notes = (project && project.script_notes) || [];
  $("#script-meta").textContent = words
    ? `${words} mots · ~${seconds} s de voix off${notes.length ? " · " + notes.join(" ") : ""}`
    : "";
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
  $("#health").innerHTML =
    tag("ffmpeg", h.ffmpeg) + tag("yt-dlp", h.yt_dlp) +
    tag(h.anthropic_key ? "clé Claude" : "script manuel", true) +
    tag(`${h.music_count} musique(s)`, true) +
    (h.auth ? tag("protégé", true) : "");
  if (!h.ffmpeg || !h.yt_dlp) {
    alertBox("Dépendances manquantes : installe ffmpeg et `pip install -r requirements.txt`.");
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

async function loadVoices() {
  const { voices, default: def } = await api("/api/voices");
  $("#voice").innerHTML = voices
    .map((v) => `<option value="${v.id}">${escapeHtml(v.label)}</option>`).join("");
  $("#voice").value = def;
}

async function loadPresets() {
  const { subtitles, default: def } = await api("/api/presets");
  state.presets = subtitles;
  $("#subtitle_preset").innerHTML = subtitles
    .map((p) => `<option value="${p.id}">${escapeHtml(p.label)}</option>`).join("");
  $("#subtitle_preset").value = def;
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

async function loadMusic() {
  const { tracks } = await api("/api/music");
  $("#music").innerHTML = `<option value="">Aucune</option>` +
    tracks.map((t) => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.label)}</option>`).join("");
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
  });

  $("#music_volume").addEventListener("input", (e) => {
    $("#music_volume_v").textContent = `${e.target.value}%`;
    paintRange(e.target);
  });
  $("#mask_height_ratio").addEventListener("input", (e) => {
    $("#mask_height_ratio_v").textContent = `${e.target.value}%`;
    paintRange(e.target);
  });
  $("#subtitle_preset").addEventListener("change", describePreset);
  $("#mask_source_subtitles").addEventListener("change", (e) => {
    $("#mask-options").classList.toggle("hidden", !e.target.checked);
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
  .catch((err) => alertBox(err.message));
