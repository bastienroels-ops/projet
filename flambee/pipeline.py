"""Orchestration des tâches longues (téléchargement, analyse, rendu).

Ces fonctions tournent dans un thread de fond ; l'avancement est écrit dans
`project.job` et l'interface interroge `/api/projects/{id}` pour l'afficher.
Chaque projet possède un drapeau d'annulation : l'utilisateur peut couper un
rendu en cours sans attendre la fin de ffmpeg.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from . import (analyzer, assembler, calage, config, downloader, solo,
               subtitles, trimmer, voice, voicestudio)
from . import account, users
from .media import Cancelled, MediaError, detect_encoder, ensure_tools, probe
from .project import Project

log = logging.getLogger(__name__)

TAIL_SILENCE = 0.6   # secondes de marge après la fin de la voix off

# --- Annulation -----------------------------------------------------------
_cancels: dict[str, threading.Event] = {}
_lock = threading.Lock()


def cancel_token(project_id: str) -> threading.Event:
    with _lock:
        token = _cancels.get(project_id)
        if token is None or token.is_set():
            token = threading.Event()
            _cancels[project_id] = token
        return token


def request_cancel(project_id: str) -> bool:
    """Demande l'arrêt de la tâche en cours pour ce projet."""
    with _lock:
        token = _cancels.get(project_id)
    if token is None:
        return False
    token.set()
    return True


def _clear_cancel(project_id: str) -> None:
    with _lock:
        _cancels.pop(project_id, None)


# --- Étapes 1 et 2 --------------------------------------------------------
def run_download(project: Project, urls: list[str]) -> None:
    """Télécharge les sources, extrait les accroches et analyse les plans."""
    token = cancel_token(project.id)
    solo_avant = project.solo
    project.urls = urls
    project.sources = []
    project.analyses = {}
    _oublier_les_paroles(project)
    project.ensure_dirs()
    project.set_job("download", "running", progress=0.02,
                    message=f"Téléchargement de {len(urls)} vidéo(s)…")

    try:
        total = len(urls)

        def on_progress(done: int, _total: int, source: downloader.Source) -> None:
            project.sources = sorted(
                [s for s in project.sources if s.index != source.index] + [source],
                key=lambda s: s.index,
            )
            project.set_job(
                "download", "running",
                progress=0.05 + 0.55 * done / max(1, total),
                message=f"{done}/{total} vidéo(s) téléchargée(s).",
            )

        project.sources = downloader.download_all(
            urls, project.sources_dir, on_progress=on_progress, cancel=token
        )
        _raise_if_cancelled(token)

        if not project.ready_sources:
            errors = " ".join(s.error for s in project.sources if s.error)
            raise MediaError(f"Aucune vidéo téléchargée. {errors}".strip())

        _preparer_les_sources(project, token, "download", 0.68)
        _appliquer_le_mode(project, solo_avant)

        project.set_job(
            "download", "done", progress=1.0,
            message=_bilan_des_sources(project),
        )
    except Cancelled:
        project.set_job("download", "error", message="Téléchargement annulé.",
                        error="Annulé par l'utilisateur.")
    except Exception as exc:
        log.error("Téléchargement KO : %s", traceback.format_exc())
        project.set_job("download", "error", message="Téléchargement interrompu.",
                        error=str(exc))
    finally:
        _clear_cancel(project.id)


def run_import(
    project: Project,
    paths: list[Path],
    titles: list[str] | None = None,
) -> None:
    """Intègre des vidéos importées depuis l'appareil (pellicule, Fichiers…).

    Même traitement que des vidéos téléchargées : contrôle du fichier, accroche
    de 3 s, détection des plans et score d'accroche.
    """
    token = cancel_token(project.id)
    solo_avant = project.solo
    _oublier_les_paroles(project)
    project.ensure_dirs()
    project.set_job("import", "running", progress=0.05,
                    message=f"Analyse de {len(paths)} fichier(s)…")

    try:
        sources: list[downloader.Source] = []
        for position, path in enumerate(paths, start=1):
            _raise_if_cancelled(token)
            index = position
            title = (titles[position - 1] if titles and len(titles) >= position
                     else path.stem)
            source = downloader.Source(index=index, url=f"fichier://{path.name}",
                                       path=str(path), title=title)
            try:
                info = probe(path)
            except MediaError as exc:
                source.error = f"Fichier illisible : {exc}"
                sources.append(source)
                continue

            if info.duration <= 0 or info.width <= 0:
                source.error = "Ce fichier ne contient pas de vidéo exploitable."
                sources.append(source)
                continue

            source.duration = info.duration
            source.width = info.width
            source.height = info.height
            source.has_audio = info.has_audio
            source.warnings = downloader._check_source(
                source, solo=len(paths) == 1)
            sources.append(source)

        project.sources = sources
        project.urls = [s.url for s in sources]
        if not project.ready_sources:
            errors = " ".join(s.error for s in sources if s.error)
            raise MediaError(f"Aucune vidéo exploitable. {errors}".strip())

        _preparer_les_sources(project, token, "import", 0.5)
        _appliquer_le_mode(project, solo_avant)
        project.set_job("import", "done", progress=1.0,
                        message=_bilan_des_sources(project))
    except Cancelled:
        project.set_job("import", "error", message="Import annulé.",
                        error="Annulé par l'utilisateur.")
    except Exception as exc:
        log.error("Import KO : %s", traceback.format_exc())
        project.set_job("import", "error", message="Import interrompu.",
                        error=str(exc))
    finally:
        _clear_cancel(project.id)


def _preparer_les_sources(project: Project, token: threading.Event,
                          tache: str, progression: float) -> None:
    """Prépare les vidéos arrivées, selon qu'il faut les monter ou les retoucher.

    À plusieurs, on extrait l'accroche de chacune et on repère leurs plans : de
    quoi choisir laquelle ouvre le montage. Seule, une vidéo n'a rien à
    départager ni à découper — l'analyser serait du temps perdu, et l'étape
    Accroche n'aurait pas de sens : on passe directement au style.
    """
    if project.solo:
        source = project.ready_sources[0]
        project.hook_index = project.recommended_hook = source.index
        project.step = max(project.step, 3)
        return

    # Accroches et analyse tournent ensemble : ce sont deux décodages
    # indépendants, autant occuper tous les cœurs d'un coup.
    project.set_job(tache, "running", progress=progression,
                    message="Extraction des accroches et analyse des plans…")
    with ThreadPoolExecutor(max_workers=2) as pool:
        hooks = pool.submit(trimmer.extract_hooks, project.sources,
                            project.hooks_dir)
        analyses = pool.submit(analyzer.analyze_all, project.sources)
        hooks.result()
        project.analyses = {
            index: analysis.to_dict()
            for index, analysis in analyses.result().items()
        }

    _raise_if_cancelled(token)
    _apply_scores(project)
    project.step = max(project.step, 2)


def _bilan_des_sources(project: Project) -> str:
    if project.solo:
        return "Vidéo prête : tu peux la retoucher."
    return f"{len(project.ready_sources)} vidéo(s) prête(s)."


def _appliquer_le_mode(project: Project, solo_avant: bool) -> None:
    """Règle les options qui dépendent du mode, quand le mode change.

    Retouchée seule, une vidéo garde son son et son cadre : ni ambiance à
    doser, ni coupes à caler, ni travelling qu'on n'a pas demandé. Montée avec
    d'autres, elle retrouve les réglages d'un montage. Rien n'est touché tant
    que le mode ne change pas : un choix de l'utilisateur ne doit pas sauter
    parce qu'il relance un téléchargement.
    """
    if project.solo == solo_avant:
        return
    defauts = config.RenderSettings()
    if project.solo:
        reglages = dict(motion=False, scene_aware=False, keep_source_audio=True)
    else:
        reglages = dict(motion=defauts.motion, scene_aware=defauts.scene_aware,
                        keep_source_audio=defauts.keep_source_audio)
    project.settings = replace(project.settings, **reglages)


def _oublier_les_paroles(project: Project) -> None:
    """De nouvelles vidéos, de nouvelles paroles : les anciennes ne valent plus."""
    project.transcript_words = []
    project.transcript_done = False


def voix_off_active(project: Project) -> bool:
    """Une vidéo seule n'a de voix off que si on lui en a donné une : un texte
    à lire, ou l'enregistrement importé. En montage, il y en a toujours une."""
    if not project.solo:
        return True
    return bool(project.script.strip()
                or project.settings.voice == voicestudio.VOICE_ID)


def _reglages_du_rendu(project: Project) -> config.RenderSettings:
    """Les réglages tels que l'assemblage doit les lire.

    Le son d'origine n'est jamais retiré d'office. Sans voix off, l'assemblage
    le laisse à 100 % (voir `assembler.source_gain`) ; avec, il passe au
    niveau choisi par l'utilisateur.
    """
    return project.settings


def run_transcription(project: Project) -> None:
    """Écoute la vidéo et en tire les paroles, pour les sous-titres."""
    token = cancel_token(project.id)
    project.set_job("transcribe", "running", progress=0.05,
                    message="Écoute de la vidéo…")
    try:
        if not project.solo:
            raise MediaError("La transcription ne concerne que le mode une "
                             "seule vidéo.")
        source = project.ready_sources[0]
        mots = solo.paroles_de(source.path, project.dir,
                               has_audio=source.has_audio)
        _raise_if_cancelled(token)
        project.transcript_words = [mot.to_dict() for mot in mots]
        project.transcript_done = True
        project.set_job(
            "transcribe", "done", progress=1.0,
            message=f"{len(mots)} mot(s) reconnu(s)." if mots
            else "Aucune parole détectée : écris le texte, ou passe.",
        )
    except Cancelled:
        project.set_job("transcribe", "error", message="Transcription annulée.",
                        error="Annulé par l'utilisateur.")
    except Exception as exc:
        log.error("Transcription KO : %s", traceback.format_exc())
        project.set_job("transcribe", "error",
                        message="Transcription impossible.", error=str(exc))
    finally:
        _clear_cancel(project.id)


def _apply_scores(project: Project) -> None:
    """Reporte les scores d'accroche sur les sources et propose la meilleure."""
    best_index, best_score = None, -1.0
    for source in project.sources:
        analysis = project.analyses.get(source.index) or project.analyses.get(
            str(source.index)
        )
        if not analysis:
            continue
        source.hook_score = float(analysis.get("hook_score") or 0.0)
        if source.ok and source.hook_score > best_score:
            best_index, best_score = source.index, source.hook_score
    project.recommended_hook = best_index


# --- Étape 5 --------------------------------------------------------------
def run_render(project: Project, *, fast: bool = False) -> None:
    """Voix off, sous-titres, découpe, mixage et export — en une passe ffmpeg."""
    token = cancel_token(project.id)
    project.ensure_dirs()
    project.set_job("render", "running", progress=0.02, message="Préparation…")

    try:
        missing = ensure_tools()
        if missing:
            raise MediaError(
                f"Outil manquant : {', '.join(missing)}. Installe ffmpeg "
                "(ex. `brew install ffmpeg` ou `apt install ffmpeg`)."
            )
        if not project.ready_sources:
            raise MediaError("Aucune vidéo source exploitable.")
        # Retouchée seule, la vidéo n'a pas besoin de script : ce sont ses
        # propres paroles qui parlent.
        if (not project.solo and not project.script.strip()
                and project.settings.voice != voicestudio.VOICE_ID):
            raise MediaError("Aucun script validé.")

        if project.solo:
            target_duration = _preparer_la_retouche(project, token)
        else:
            target_duration = _preparer_le_montage(project, token)

        # 4. Rendu ---------------------------------------------------------
        encoder = detect_encoder()
        fmt = config.PREVIEW_FORMAT if fast else config.FORMAT
        project.set_job(
            "render", "running", progress=0.15,
            message=f"{'Aperçu' if fast else 'Montage'} et encodage "
                    + (f"({fmt.size}, {encoder.name})…" if project.solo else
                       f"({len(project.segments)} plans, {fmt.size}, {encoder.name})…"),
        )

        def on_progress(fraction: float) -> None:
            project.set_job(
                "render", "running",
                progress=0.15 + 0.84 * fraction,
                message=f"Encodage {fraction * 100:.0f} % "
                        f"({target_duration:.0f}s, {fmt.size}, {encoder.name})",
            )

        if fast:
            out_path = project.dir / "apercu.mp4"
        else:
            # Un sous-dossier par compte : sans lui, tous les rendus finaux
            # de tous les utilisateurs tombaient dans le même `output/`, seul
            # espace à ne pas suivre le cloisonnement appliqué partout
            # ailleurs (work/utilisateurs/<id>/…). Deux comptes rendant au
            # même moment un sujet au nom proche pouvaient alors se marcher
            # dessus, silencieusement.
            out_dir = config.OUTPUT_DIR / (str(project.owner) if project.owner else "local")
            out_dir.mkdir(parents=True, exist_ok=True)
            nom = project.topic or (
                project.ready_sources[0].title if project.solo else "")
            out_path = out_dir / assembler.output_name(nom)

        result = _render_with_fallback(
            project, out_path,
            duration=target_duration, encoder=encoder, fast=fast, fmt=fmt,
            on_progress=on_progress, cancel=token,
        )

        if fast:
            project.preview_path = str(out_path)
        else:
            project.output_path = str(out_path)
            proprietaire = users.par_id(project.owner) if project.owner else None
            if proprietaire:
                account.noter(proprietaire, "rendu", out_path.name)  # un crédit
        project.step = 5
        coupee = (f" Attention : la voix off ({project.voice_duration:.0f}s) dépasse "
                  f"la vidéo et a été coupée à {target_duration:.0f}s."
                  if project.solo and project.voice_path
                  and project.voice_duration > target_duration + 0.3 else "")
        project.set_job(
            "render", "done", progress=1.0,
            message=f"{'Aperçu prêt' if fast else 'Vidéo prête'} : {out_path.name} — "
                    f"{result.duration:.0f}s, {result.size_bytes / 1e6:.1f} Mo, "
                    f"rendu en {result.elapsed:.0f}s ({result.encoder}, {fmt.size})"
                    + coupee,
        )
    except Cancelled:
        project.set_job("render", "error", message="Rendu annulé.",
                        error="Annulé par l'utilisateur.")
    except Exception as exc:
        log.error("Rendu KO : %s", traceback.format_exc())
        project.set_job("render", "error", message="Rendu interrompu.", error=str(exc))
    finally:
        _clear_cancel(project.id)


def _preparer_le_montage(project: Project, token: threading.Event) -> float:
    """Voix off, sous-titres et plan de montage. Retourne la durée visée."""
    settings = project.settings
    # 1. Voix off ----------------------------------------------------
    # Rendue une seule fois par script : un rendu final qui suit un aperçu
    # réutilise la piste déjà synthétisée (et son minutage au mot).
    track = _voice_track(project, cached_only=False)
    project.voice_path = track.path
    project.voice_duration = track.duration
    target_duration = track.duration + TAIL_SILENCE
    _raise_if_cancelled(token)

    # 2. Sous-titres ---------------------------------------------------
    if settings.subtitles:
        project.set_job("render", "running", progress=0.12,
                        message="Sous-titres animés…")
        project.subtitle_path = str(subtitles.write_ass(
            track.words,
            project.dir / "subtitles.ass",
            style=subtitles.placer(
                config.subtitle_style(settings.subtitle_preset),
                settings, config.FORMAT),
            offset=track.lead_in,
            max_duration=target_duration,
        ))
    else:
        project.subtitle_path = ""

    # 3. Plan de montage ----------------------------------------------
    project.segments = trimmer.plan_segments(
        project.sources,
        hook_index=project.hook_index or project.ready_sources[0].index,
        target_duration=target_duration,
        scenes=project.scene_cuts() if settings.scene_aware else None,
    )
    _raise_if_cancelled(token)
    return target_duration


def _preparer_la_retouche(project: Project, token: threading.Event) -> float:
    """Sous-titres et voix off facultative d'une vidéo seule.

    Retourne sa durée : elle est gardée entière. Pas de plan de montage : un
    seul extrait, du début à la fin, dont le son est celui de la vidéo — auquel
    s'ajoute la voix off si l'utilisateur en a demandé une.
    """
    source = project.ready_sources[0]
    settings = project.settings
    try:
        duration = probe(source.path).duration or source.duration
    except MediaError:
        duration = source.duration
    if duration <= 0:
        raise MediaError("Durée de la vidéo inconnue.")

    # 1. Voix off (facultative) ----------------------------------------
    track = None
    if voix_off_active(project):
        track = _voice_track(project, cached_only=False)
        project.voice_path = track.path
        project.voice_duration = track.duration
        if track.duration > duration + 0.3:
            log.warning("Voix off de %.1f s sur une vidéo de %.1f s : coupée.",
                        track.duration, duration)
    else:
        project.voice_path = ""
        project.voice_duration = 0.0
    _raise_if_cancelled(token)

    # 2. Sous-titres ---------------------------------------------------
    # Avec une voix off, ce qu'on entend au premier plan est elle : les
    # sous-titres la suivent, comme en montage. Sans, ce sont les paroles de
    # la vidéo.
    mots: list[voice.Word] = []
    decalage = 0.0
    if settings.subtitles:
        if track is not None:
            mots, decalage = track.words, track.lead_in
        else:
            if not project.transcript_done:
                # L'étape Texte n'a pas été visitée : on écoute la vidéo.
                project.set_job("render", "running", progress=0.06,
                                message="Écoute de la vidéo pour les sous-titres…")
                paroles = solo.paroles_de(source.path, project.dir,
                                          has_audio=source.has_audio)
                project.transcript_words = [m.to_dict() for m in paroles]
                project.transcript_done = True
            _raise_if_cancelled(token)
            mots = [voice.Word(**mot) for mot in project.transcript_words]

    if mots:
        project.set_job("render", "running", progress=0.12,
                        message="Sous-titres animés…")
        project.subtitle_path = str(subtitles.write_ass(
            mots,
            project.dir / "subtitles.ass",
            style=subtitles.placer(
                config.subtitle_style(settings.subtitle_preset),
                settings, config.FORMAT),
            offset=decalage,
            max_duration=duration,
        ))
    else:
        project.subtitle_path = ""      # rien n'est dit : rien à afficher

    project.segments = [trimmer.Segment(source_index=source.index, start=0.0,
                                        duration=round(duration, 3),
                                        is_hook=True)]
    _raise_if_cancelled(token)
    return duration


# Le texte du filigrane. Discret, mais reconnaissable : c'est ce qui distingue
# un rendu d'essai d'un rendu payant, et ce que la formule Créateur retire.
FILIGRANE = "Flambée"


def filigrane_pour(project: Project) -> str:
    """Le filigrane à incruster, ou une chaîne vide si la formule l'enlève.

    Un projet dont on ne retrouve plus le propriétaire est traité comme un
    essai : mieux vaut un filigrane de trop qu'un rendu payant offert.
    """
    utilisateur = users.par_id(project.owner) if project.owner else None
    if utilisateur is None:
        return FILIGRANE
    return FILIGRANE if account.filigrane(utilisateur) else ""


def _render_with_fallback(
    project: Project,
    out_path: Path,
    *,
    duration: float,
    encoder,
    fast: bool,
    fmt: config.VideoFormat,
    on_progress,
    cancel: threading.Event,
) -> assembler.AssemblyResult:
    """Tente le rendu en une passe, et bascule sur le repli si ffmpeg refuse."""
    settings = _reglages_du_rendu(project)
    chemin_musique = music_path(settings.music)
    chemin_fond = fond_path(settings.split_clip, project)
    subtitle_path = Path(project.subtitle_path) if project.subtitle_path else None
    voice_path = Path(project.voice_path) if project.voice_path else None
    marque = filigrane_pour(project)

    try:
        return assembler.render(
            project.segments, project.sources, out_path,
            voice_path=voice_path, subtitle_path=subtitle_path,
            music_path=chemin_musique, settings=settings, duration=duration,
            fmt=fmt, encoder=encoder, fast=fast, watermark=marque,
            split_path=chemin_fond, on_progress=on_progress, cancel=cancel,
        )
    except Cancelled:
        raise
    except MediaError as exc:
        log.warning("Rendu en une passe impossible (%s), repli multi-passes.", exc)
        project.set_job("render", "running", progress=0.2,
                        message="Montage (mode compatible)…")

        clips: list[Path] = []
        for position, segment in enumerate(project.segments, start=1):
            _raise_if_cancelled(cancel)
            source = project.source(segment.source_index)
            if source is None:
                continue
            clip = project.clips_dir / f"clip_{position:03d}.mp4"
            trimmer.render_segment(segment, source, clip, settings=settings, fmt=fmt)
            clips.append(clip)
            project.set_job(
                "render", "running",
                progress=0.2 + 0.5 * position / max(1, len(project.segments)),
                message=f"Extrait {position}/{len(project.segments)}…",
            )

        montage = assembler.concat_clips(clips, project.dir / "montage.mp4")
        return assembler.finalize(
            montage, out_path, voice_path=voice_path, subtitle_path=subtitle_path,
            music_path=chemin_musique, settings=settings, duration=duration,
            fmt=fmt, encoder=encoder, watermark=marque,
            split_path=chemin_fond, on_progress=on_progress, cancel=cancel,
        )


def _voice_signature(project: Project) -> str:
    """Empreinte du script et des réglages de voix."""
    settings = project.settings
    payload = "|".join([
        project.script.strip(), settings.voice, settings.voice_rate,
        settings.voice_pitch,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Un verrou par projet autour de la synthèse. Sans lui, un rendu lancé
# pendant la préparation de la voix la referait en parallèle : deux appels
# réseau, deux écritures dans le même fichier, et le minutage de celui qui
# finit second.
_verrous_voix: dict[str, threading.Lock] = {}
_garde_voix = threading.Lock()


def _verrou_voix(project_id: str) -> threading.Lock:
    with _garde_voix:
        return _verrous_voix.setdefault(project_id, threading.Lock())


def prechauffer_la_voix(project: Project) -> None:
    """Fabrique la voix dès que le script est validé, sans rien annoncer.

    Mesuré : la synthèse et son calage pèsent plus du quart de l'attente d'un
    aperçu. Or le script est connu une étape avant le rendu, et l'utilisateur
    passe ce temps-là à lire son récapitulatif. Autant l'occuper.

    Cette tâche ne touche pas à l'état du projet : elle écrit la voix dans le
    cache et s'arrête là. Si elle échoue — réseau coupé, voix retirée —, le
    rendu la refera et signalera l'erreur lui-même, à un moment où
    l'utilisateur attend une réponse.
    """
    try:
        if project.settings.voice == voicestudio.VOICE_ID:
            return                           # rien à synthétiser
        if not project.script.strip():
            return
        _voice_track(project, silencieux=True)
        project.save()
        log.info("Voix préparée d'avance pour le projet %s.", project.id)
    except Exception as exc:                 # jamais rien casser en arrière-plan
        log.info("Préparation de la voix impossible (%s) : le rendu s'en "
                 "chargera.", exc)


def _voice_track(project: Project, *, cached_only: bool = False,
                 silencieux: bool = False) -> voice.VoiceTrack:
    """Retourne la voix off, depuis le cache du projet si elle est à jour.

    `silencieux` : ne rien écrire dans l'état de la tâche. C'est ce que fait
    la préparation d'avance, qui tourne pendant que l'utilisateur regarde une
    page où aucune barre de progression n'a de sens.
    """
    # Voix importée : le fichier existe déjà, son minutage vient de la
    # transcription. Rien à synthétiser, et le script n'entre pas en jeu.
    def annoncer(progress: float, message: str) -> None:
        if not silencieux:
            project.set_job("render", "running", progress=progress,
                            message=message)

    if project.settings.voice == voicestudio.VOICE_ID:
        piste = voicestudio.piste(project.owner)
        annoncer(0.10, "Voix importée : montage calé sur ton enregistrement.")
        return piste

    signature = _voice_signature(project)
    path = project.dir / "voice.mp3"

    def depuis_le_cache() -> voice.VoiceTrack | None:
        if (project.voice_signature == signature and project.voice_words
                and path.exists() and project.voice_duration > 0):
            return voice.VoiceTrack(
                path=str(path),
                duration=project.voice_duration,
                words=[voice.Word(**word) for word in project.voice_words],
                voice=project.settings.voice,
                lead_in=project.voice_lead_in,
            )
        return None

    piste = depuis_le_cache()
    if piste is not None:
        log.info("Voix off réutilisée (script inchangé).")
        annoncer(0.10, "Voix off réutilisée (script inchangé).")
        return piste

    if cached_only:
        raise MediaError("Aucune voix off en cache.")

    # Le verrou fait attendre un rendu lancé pendant la préparation d'avance
    # — le temps qu'elle finisse, pas le temps d'une seconde synthèse. D'où
    # la relecture du cache une fois le verrou obtenu : entre-temps, le
    # travail a peut-être été fait.
    with _verrou_voix(project.id):
        piste = depuis_le_cache()
        if piste is not None:
            log.info("Voix off préparée d'avance : rien à refaire.")
            annoncer(0.10, "Voix off prête.")
            return piste
        return _synthetiser(project, path, signature, annoncer)


def _synthetiser(project: Project, path: Path, signature: str,
                 annoncer) -> voice.VoiceTrack:
    """La synthèse elle-même. Appelée sous verrou, jamais deux fois de front."""
    annoncer(0.06, "Génération de la voix off…")
    track = voice.synthesize(
        project.script, path,
        voice=project.settings.voice,
        rate=project.settings.voice_rate,
        pitch=project.settings.voice_pitch,
    )
    # Le minutage d'edge-tts ne décrit pas le fichier livré : il ne contient
    # aucune pause, là où l'audio en a trois secondes sur dix. On le relève
    # donc sur le son lui-même. Sans condition : ça ne coûte qu'une passe
    # ffmpeg, et c'est mesuré dix fois plus juste.
    annoncer(0.08, "Calage des sous-titres sur la voix…")
    cales = calage.caler(track.words, path)
    if cales is not track.words:
        # Les mots calés sont déjà dans le temps de l'audio : le décalage du
        # silence initial n'a plus lieu d'être, il ferait double emploi.
        track = replace(track, words=cales, lead_in=0.0)

    # Tout ce qui décide de la relecture du cache s'écrit ici, y compris la
    # durée : elle n'était posée que par `run_render`, si bien qu'une voix
    # préparée d'avance échouait au contrôle et se refaisait entièrement.
    project.voice_signature = signature
    project.voice_path = str(path)
    project.voice_duration = track.duration
    project.voice_words = [word.to_dict() for word in track.words]
    project.voice_lead_in = track.lead_in
    return track


def _raise_if_cancelled(token: threading.Event) -> None:
    if token.is_set():
        raise Cancelled("Tâche annulée.")


# --- Bibliothèque musicale ------------------------------------------------
def music_path(name: str | None) -> Path | None:
    """Retourne le chemin d'une musique de la bibliothèque locale.

    Public : l'API s'en sert pour servir un extrait, et la garde contre les
    chemins hors bibliothèque doit rester le seul point de passage."""
    if not name:
        return None
    candidate = (config.MUSIC_DIR / name).resolve()
    if not str(candidate).startswith(str(config.MUSIC_DIR.resolve())):
        return None                      # jamais de chemin hors bibliothèque
    return candidate if candidate.exists() else None


# Durées déjà mesurées, indexées par (nom, taille, date). ffprobe coûte un
# processus par fichier : sans ce cache, afficher la bibliothèque en lancerait
# autant à chaque ouverture de l'étape Style.
_DUREES_MUSIQUE: dict[tuple[str, int, int], float] = {}


def duree_musique(path: Path) -> float:
    """Durée d'une piste en secondes, 0 si ffprobe ne peut rien en dire."""
    stat = path.stat()
    cle = (path.name, stat.st_size, int(stat.st_mtime))
    if cle not in _DUREES_MUSIQUE:
        try:
            _DUREES_MUSIQUE[cle] = probe(path).duration
        except (MediaError, OSError, ValueError):
            _DUREES_MUSIQUE[cle] = 0.0
    return _DUREES_MUSIQUE[cle]


# --- Fonds d'écran scindé -------------------------------------------------
# Identifiant réservé au fichier déposé sur le projet lui-même, par opposition
# aux fonds de la bibliothèque partagée.
FOND_DU_PROJET = "@projet"
FOND_DU_PROJET_FICHIER = "fond.mp4"


def run_fond_lien(project: Project, url: str) -> None:
    """Télécharge la vidéo du bas depuis un lien, pour ce projet.

    Même chemin que les sources — yt-dlp, mêmes limites, mêmes refus de
    plateforme — mais le résultat ne rejoint pas le montage : il devient la
    bande d'à côté. D'où une tâche à part, qui ne touche ni aux sources, ni à
    l'accroche choisie, ni au plan de montage déjà calculé.
    """
    token = cancel_token(project.id)
    project.ensure_dirs()
    project.set_job("fond", "running", progress=0.05,
                    message="Téléchargement de la deuxième vidéo…")

    travail = project.dir / ".fond"
    try:
        shutil.rmtree(travail, ignore_errors=True)
        travail.mkdir(parents=True, exist_ok=True)
        source = downloader.download_one(url, travail, 0, cancel=token)
        _raise_if_cancelled(token)

        if source.error or not source.path:
            raise MediaError(source.error or "Téléchargement impossible.")

        destination = project.dir / FOND_DU_PROJET_FICHIER
        destination.unlink(missing_ok=True)
        # `replace` plutôt que `rename` : la destination peut exister si l'on
        # remplace un fond déjà déposé, et `rename` échoue alors sous Windows.
        Path(source.path).replace(destination)

        project.settings = replace(project.settings,
                                   split_clip=FOND_DU_PROJET)
        project.set_job("fond", "done", progress=1.0,
                        message=f"Deuxième vidéo prête : {source.title or url}")
    except Cancelled:
        project.set_job("fond", "idle", progress=0.0, message="Annulé.")
    except (MediaError, downloader.DownloadError, OSError) as exc:
        log.warning("Fond depuis un lien impossible : %s", exc)
        project.set_job("fond", "error", message="Deuxième vidéo :",
                        error=str(exc))
    finally:
        # Rien d'autre ici : `set_job` sauvegarde déjà, et une écriture après
        # l'annonce de l'état final court après un projet dont l'appelant se
        # croit déjà libéré.
        shutil.rmtree(travail, ignore_errors=True)


def fond_path(nom: str | None, projet: Project | None = None) -> Path | None:
    """Chemin du fond d'écran scindé. Même garde que pour les musiques."""
    if not nom:
        return None
    if nom == FOND_DU_PROJET:
        if projet is None:
            return None
        chemin = projet.dir / FOND_DU_PROJET_FICHIER
        return chemin if chemin.exists() else None
    candidat = (config.FONDS_DIR / nom).resolve()
    if not str(candidat).startswith(str(config.FONDS_DIR.resolve())):
        return None                      # jamais de chemin hors bibliothèque
    return candidat if candidat.exists() else None


def list_fonds() -> list[dict[str, object]]:
    """Les boucles disponibles pour la bande du bas (assets/fonds)."""
    fonds = []
    dossier = config.FONDS_DIR
    for chemin in sorted(dossier.iterdir()) if dossier.exists() else []:
        if chemin.is_file() and chemin.suffix.lower() in config.UPLOAD_EXTENSIONS:
            fonds.append({"id": chemin.name,
                          "label": chemin.stem.replace("_", " "),
                          "duree": round(duree_musique(chemin), 1)})
    return fonds


def list_music() -> list[dict[str, object]]:
    """Bibliothèque de musiques locales (assets/music)."""
    extensions = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
    tracks = []
    for path in sorted(config.MUSIC_DIR.iterdir()) if config.MUSIC_DIR.exists() else []:
        if path.is_file() and path.suffix.lower() in extensions:
            tracks.append({"id": path.name,
                           "label": path.stem.replace("_", " "),
                           "duree": round(duree_musique(path), 1)})
    return tracks
