"""
Skill File Watcher (Hot-Reload)

Watches .claude/skills/*.md files using watchdog and automatically
reloads skills when any file is created, modified, or deleted.

Usage (in app startup)::

    from backend.skills.skill_watcher import start_skill_watcher

    watcher = start_skill_watcher()   # starts background thread
    # ...
    watcher.stop()                    # on shutdown

The watcher calls ``reload_skills()`` from the module-level
SkillLoader singleton, so all subsequent calls to
``get_skill_loader().find_triggered()`` will use the updated skills.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Try to import watchdog; fall back gracefully if unavailable
# ──────────────────────────────────────────────────────────

try:
    from watchdog.events import (
        FileCreatedEvent,
        FileDeletedEvent,
        FileModifiedEvent,
        FileMovedEvent,
        FileSystemEventHandler,
    )
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
    logger.warning(
        "[SkillWatcher] watchdog not installed — hot-reload disabled. "
        "Install with: pip install watchdog"
    )


# ──────────────────────────────────────────────────────────
# Debounce helper
# ──────────────────────────────────────────────────────────


class _Debouncer:
    """
    Coalesces rapid file-system events into a single callback.

    This prevents multiple reloads when an editor saves a file in
    several write operations (common on Windows).
    """

    def __init__(self, delay: float, callback: Callable[[], None]):
        self._delay = delay
        self._callback = callback
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def trigger(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self._callback()
        except Exception as exc:
            logger.error(f"[SkillWatcher] Reload callback error: {exc}", exc_info=True)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


# ──────────────────────────────────────────────────────────
# Watchdog event handler
# ──────────────────────────────────────────────────────────


def _make_handler(debouncer: "_Debouncer"):
    """Create a watchdog FileSystemEventHandler that triggers debouncer."""
    if not _WATCHDOG_AVAILABLE:
        return None

    class _Handler(FileSystemEventHandler):
        def _relevant(self, event) -> bool:
            path = getattr(event, "src_path", "") or ""
            return (
                not event.is_directory
                and path.endswith(".md")
                and not Path(path).name.startswith("_")
                and Path(path).name.upper() != "README.MD"
            )

        def on_created(self, event: FileCreatedEvent) -> None:
            if self._relevant(event):
                logger.info(f"[SkillWatcher] New skill file: {event.src_path}")
                debouncer.trigger()

        def on_modified(self, event: FileModifiedEvent) -> None:
            if self._relevant(event):
                logger.info(f"[SkillWatcher] Skill file modified: {event.src_path}")
                debouncer.trigger()

        def on_deleted(self, event: FileDeletedEvent) -> None:
            if self._relevant(event):
                logger.info(f"[SkillWatcher] Skill file deleted: {event.src_path}")
                debouncer.trigger()

        def on_moved(self, event: FileMovedEvent) -> None:
            src = getattr(event, "src_path", "")
            dest = getattr(event, "dest_path", "")
            if src.endswith(".md") or dest.endswith(".md"):
                logger.info(
                    f"[SkillWatcher] Skill file moved: {src} → {dest}"
                )
                debouncer.trigger()

    return _Handler()


# ──────────────────────────────────────────────────────────
# SkillWatcher
# ──────────────────────────────────────────────────────────


def _default_reload() -> None:
    """Default reload callback — reloads the global SkillLoader singleton."""
    from backend.skills.skill_loader import reload_skills

    skills = reload_skills()
    logger.info(f"[SkillWatcher] Reloaded {len(skills)} skill(s): {[s.name for s in skills]}")


class SkillWatcher:
    """
    Background thread that watches the skills directory for changes
    and reloads skills automatically.

    Attributes:
        skills_dir: Path being watched
        is_running: True while the watcher is active
    """

    def __init__(
        self,
        skills_dir: Optional[str] = None,
        on_change: Optional[Callable[[], None]] = None,
        debounce_delay: float = 0.8,
    ):
        """
        Args:
            skills_dir:     Path to watch (default: .claude/skills/)
            on_change:      Callback invoked after debounce period.
                            Defaults to ``reload_skills()``.
            debounce_delay: Seconds to wait before firing reload after
                            the last file-system event (default 0.8s).
        """
        if skills_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            skills_dir = str(project_root / ".claude" / "skills")

        self.skills_dir = Path(skills_dir)
        self._callback = on_change or _default_reload
        self._debouncer = _Debouncer(debounce_delay, self._callback)
        self._observer: Optional[object] = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        """
        Start the background watcher thread.

        Returns:
            True if started successfully, False if watchdog is unavailable
            or directory does not exist.
        """
        if not _WATCHDOG_AVAILABLE:
            logger.warning("[SkillWatcher] watchdog not available, cannot start")
            return False

        if not self.skills_dir.exists():
            logger.warning(
                f"[SkillWatcher] Skills dir not found: {self.skills_dir} — "
                "hot-reload disabled"
            )
            return False

        handler = _make_handler(self._debouncer)
        observer = Observer()
        observer.schedule(handler, str(self.skills_dir), recursive=True)
        observer.daemon = True
        observer.start()

        self._observer = observer
        self._running = True
        logger.info(
            f"[SkillWatcher] Started watching: {self.skills_dir} "
            f"(debounce={self._debouncer._delay}s)"
        )
        return True

    def stop(self) -> None:
        """Stop the watcher and cancel any pending debounced reload."""
        self._debouncer.cancel()
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[attr-defined]
                self._observer.join(timeout=3)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(f"[SkillWatcher] Error stopping observer: {exc}")
            self._observer = None
        self._running = False
        logger.info("[SkillWatcher] Stopped")

    def __enter__(self) -> "SkillWatcher":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


# ──────────────────────────────────────────────────────────
# Module-level convenience
# ──────────────────────────────────────────────────────────

_watcher: Optional[SkillWatcher] = None


def start_skill_watcher(
    skills_dir: Optional[str] = None,
    on_change: Optional[Callable[[], None]] = None,
) -> SkillWatcher:
    """
    Start (or restart) the global skill file watcher.

    Typically called once during application startup::

        from backend.skills.skill_watcher import start_skill_watcher
        start_skill_watcher()

    Returns:
        The running SkillWatcher instance.
    """
    global _watcher
    if _watcher is not None and _watcher.is_running:
        _watcher.stop()

    _watcher = SkillWatcher(skills_dir=skills_dir, on_change=on_change)
    _watcher.start()
    return _watcher


def stop_skill_watcher() -> None:
    """Stop the global skill watcher (called on app shutdown)."""
    global _watcher
    if _watcher is not None:
        _watcher.stop()
        _watcher = None
