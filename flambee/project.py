"""État d'un projet vidéo, persisté en JSON (pas de base de données)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import config
from .downloader import Source
from .trimmer import Segment

_LOCK = threading.RLock()


@dataclass
class JobState:
    """Suivi d'une tâche longue (téléchargement, rendu…)."""

    name: str = ""
    state: str = "idle"        # idle | running | done | error
    progress: float = 0.0      # 0 → 1
    message: str = ""
    error: str = ""
    updated_at: float = field(default_factory=time.time)

    @property
    def running(self) -> bool:
        return self.state == "running"


@dataclass
class Project:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    owner: int = 0                     # identifiant du compte propriétaire
    created_at: float = field(default_factory=time.time)
    step: int = 1
    urls: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    hook_index: int | None = None
    recommended_hook: int | None = None
    analyses: dict[int, dict] = field(default_factory=dict)
    settings: config.RenderSettings = field(default_factory=config.RenderSettings)
    topic: str = ""
    instructions: str = ""
    script: str = ""
    segments: list[Segment] = field(default_factory=list)
    voice_path: str = ""
    voice_duration: float = 0.0
    voice_signature: str = ""
    voice_lead_in: float = 0.0
    voice_words: list[dict] = field(default_factory=list)
    subtitle_path: str = ""
    output_path: str = ""
    preview_path: str = ""
    job: JobState = field(default_factory=JobState)

    # --- Chemins ----------------------------------------------------------
    @staticmethod
    def base_dir(owner: int) -> Path:
        """Espace de travail d'un compte. Les projets d'un utilisateur ne sont
        jamais mêlés à ceux d'un autre, ni sur le disque ni dans les URL."""
        if owner:
            return config.WORK_DIR / "utilisateurs" / str(owner) / "projets"
        return config.WORK_DIR / "projets"          # avant tout compte

    @property
    def dir(self) -> Path:
        return self.base_dir(self.owner) / self.id

    @property
    def sources_dir(self) -> Path:
        return self.dir / "sources"

    @property
    def hooks_dir(self) -> Path:
        return self.dir / "hooks"

    @property
    def clips_dir(self) -> Path:
        return self.dir / "clips"

    def ensure_dirs(self) -> None:
        for path in (self.dir, self.sources_dir, self.hooks_dir, self.clips_dir):
            path.mkdir(parents=True, exist_ok=True)

    # --- Accès ------------------------------------------------------------
    def source(self, index: int) -> Source | None:
        return next((s for s in self.sources if s.index == index), None)

    @property
    def ready_sources(self) -> list[Source]:
        return [s for s in self.sources if s.ok]

    def scene_cuts(self) -> dict[int, list[float]]:
        """Changements de plan détectés, par index de source."""
        return {
            int(index): list(analysis.get("scenes") or [])
            for index, analysis in self.analyses.items()
        }

    # --- Sérialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "owner": self.owner,
            "created_at": self.created_at,
            "step": self.step,
            "urls": self.urls,
            "sources": [s.to_dict() for s in self.sources],
            "hook_index": self.hook_index,
            "recommended_hook": self.recommended_hook,
            "analyses": {str(k): v for k, v in self.analyses.items()},
            "settings": asdict(self.settings),
            "topic": self.topic,
            "instructions": self.instructions,
            "script": self.script,
            "segments": [s.to_dict() for s in self.segments],
            "voice_path": self.voice_path,
            "voice_duration": self.voice_duration,
            "voice_signature": self.voice_signature,
            "voice_lead_in": self.voice_lead_in,
            "voice_words": self.voice_words,
            "subtitle_path": self.subtitle_path,
            "output_path": self.output_path,
            "preview_path": self.preview_path,
            "job": asdict(self.job),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        project = cls(
            id=data["id"],
            owner=int(data.get("owner") or 0),
            created_at=data.get("created_at", time.time()),
            step=data.get("step", 1),
            urls=data.get("urls", []),
            hook_index=data.get("hook_index"),
            recommended_hook=data.get("recommended_hook"),
            topic=data.get("topic", ""),
            instructions=data.get("instructions", ""),
            script=data.get("script", ""),
            voice_path=data.get("voice_path", ""),
            voice_duration=data.get("voice_duration", 0.0),
            voice_signature=data.get("voice_signature", ""),
            voice_lead_in=data.get("voice_lead_in", 0.0),
            voice_words=data.get("voice_words", []),
            subtitle_path=data.get("subtitle_path", ""),
            output_path=data.get("output_path", ""),
            preview_path=data.get("preview_path", ""),
        )
        known_source = {f.name for f in fields(Source)}
        project.sources = [
            Source(**{k: v for k, v in raw.items() if k in known_source})
            for raw in data.get("sources", [])
        ]
        project.analyses = {
            int(index): value
            for index, value in (data.get("analyses") or {}).items()
        }
        project.segments = [Segment(**raw) for raw in data.get("segments", [])]
        known = config.RenderSettings().__dict__.keys()
        project.settings = config.RenderSettings(
            **{k: v for k, v in (data.get("settings") or {}).items() if k in known}
        )
        project.job = JobState(**(data.get("job") or {}))
        return project

    def derniere_activite(self) -> float:
        """Date du dernier enregistrement, à défaut celle de la création.

        Un projet rouvert et modifié ne doit pas être effacé parce qu'il a été
        créé il y a longtemps.
        """
        fichier = self.dir / "project.json"
        try:
            return max(self.created_at, fichier.stat().st_mtime)
        except OSError:
            return self.created_at

    def save(self) -> None:
        with _LOCK:
            self.ensure_dirs()
            tmp = self.dir / "project.json.tmp"
            tmp.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.dir / "project.json")

    def set_job(
        self,
        name: str,
        state: str,
        *,
        progress: float | None = None,
        message: str = "",
        error: str = "",
    ) -> None:
        self.job.name = name
        self.job.state = state
        if progress is not None:
            self.job.progress = max(0.0, min(1.0, progress))
        self.job.message = message
        self.job.error = error
        self.job.updated_at = time.time()
        self.save()


class ProjectStore:
    """Cache mémoire + persistance disque des projets."""

    def __init__(self) -> None:
        self._cache: dict[str, Project] = {}

    def create(self, owner: int = 0) -> Project:
        project = Project(owner=owner)
        project.ensure_dirs()
        project.save()
        with _LOCK:
            self._cache[project.id] = project
        return project

    def get(self, project_id: str, owner: int = 0) -> Project | None:
        """Retourne un projet, à condition qu'il appartienne bien au compte."""
        with _LOCK:
            connu = self._cache.get(project_id)
        if connu is not None:
            return connu if connu.owner == owner else None

        path = Project.base_dir(owner) / project_id / "project.json"
        if not path.exists():
            return None
        try:
            project = Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        if project.owner != owner:
            return None
        with _LOCK:
            self._cache[project.id] = project
        return project

    def list_recent(self, limit: int = 20, owner: int = 0) -> list[Project]:
        projects = []
        for path in Project.base_dir(owner).glob("*/project.json"):
            project = self.get(path.parent.name, owner=owner)
            if project:
                projects.append(project)
        projects.sort(key=lambda p: p.created_at, reverse=True)
        return projects[:limit]

    def purge(self, owner: int, jours: int | None) -> list[str]:
        """Efface les projets d'un compte plus vieux que `jours`.

        `jours=None` ne supprime rien : c'est la conservation sans limite de la
        formule Studio. Retourne les identifiants effacés, pour le journal.
        """
        if not jours or jours <= 0:
            return []
        limite = time.time() - jours * 86400
        effaces = []
        for chemin in Project.base_dir(owner).glob("*/project.json"):
            projet = self.get(chemin.parent.name, owner=owner)
            # Un projet qu'on vient de rouvrir n'est pas un projet oublié :
            # c'est la dernière activité qui compte, pas la date de création.
            if projet and projet.derniere_activite() < limite:
                if self.delete(projet.id, owner=owner):
                    effaces.append(projet.id)
        return effaces

    def delete(self, project_id: str, owner: int = 0) -> bool:
        import shutil

        project_dir = Project.base_dir(owner) / project_id
        with _LOCK:
            self._cache.pop(project_id, None)
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
            return True
        return False


store = ProjectStore()
