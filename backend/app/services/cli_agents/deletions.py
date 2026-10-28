"""Aplicación segura del manifiesto de archivos que un worker solicita eliminar."""
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.core.logging import get_logger
from app.services.cli_agents.adapters import POINTER_DIR

MANIFEST_NAME = "borrar.txt"
PROTECTED_ROOTS = frozenset({".git", ".bipolar"})
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
log = get_logger(__name__)


@dataclass
class DeletionReport:
    deleted: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


def manifest_path(workspace: Path, job_id: str) -> Path:
    return workspace / POINTER_DIR / job_id / MANIFEST_NAME


def _normalize(line: str) -> str:
    relative = line.strip()
    if len(relative) >= 2 and relative[0] == relative[-1] == '"':
        relative = relative[1:-1].strip()
    return relative.replace("\\", "/")


def _syntax_reason(relative: str) -> str:
    parts = PurePosixPath(relative).parts
    if relative.startswith("/") or PureWindowsPath(relative).is_absolute() or _WINDOWS_DRIVE_RE.match(relative):
        return "ruta absoluta"
    if ".." in parts:
        return "contiene segmento .."
    if parts and parts[0] in PROTECTED_ROOTS:
        return "ruta protegida"
    return ""


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _filesystem_reason(root: Path, candidate: Path) -> str:
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return "no se pudo resolver"
    if not _inside(root, resolved):
        return "fuera del workspace"
    # un symlink en cualquier tramo de la ruta podría apuntar fuera del workspace
    if any(part.is_symlink() for part in (candidate, *candidate.parents) if _inside(root, part)):
        return "es un symlink"
    if not candidate.exists():
        return "no existe"
    if not candidate.is_file():
        return "no es un archivo regular"
    return ""


def _is_tracked(workspace: Path, relative: str) -> bool:
    if not (workspace / ".git").exists():
        return False
    try:
        result = subprocess.run(["git", "-C", str(workspace), "ls-files", "--error-unmatch", "--", relative],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _delete_one(workspace: Path, root: Path, relative: str, created_during_job: set[str], report: DeletionReport) -> None:
    candidate = root / Path(*PurePosixPath(relative).parts)
    reason = _syntax_reason(relative) or _filesystem_reason(root, candidate)
    # solo lo recuperable: versionado en git o creado por este mismo job
    if not reason and relative not in created_during_job and not _is_tracked(workspace, relative):
        reason = "sin respaldo en git"
    if reason:
        report.rejected.append((relative, reason))
        return
    try:
        candidate.unlink()
        report.deleted.append(relative)
    except OSError as error:
        report.rejected.append((relative, str(error)))


def _read_manifest(manifest: Path, job_id: str) -> list[str]:
    try:
        return manifest.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    except OSError as error:
        log.warning("deletion_manifest_read_failed", job=job_id, error=str(error))
        return []


def apply_deletions(workspace: Path, job_id: str, created_during_job: set[str]) -> DeletionReport:
    report = DeletionReport()
    manifest = manifest_path(workspace, job_id)
    if not manifest.exists():
        return report
    root = workspace.resolve()
    try:
        for line in _read_manifest(manifest, job_id):
            relative = _normalize(line)
            if relative:
                _delete_one(workspace, root, relative, created_during_job, report)
    finally:
        try:
            manifest.unlink(missing_ok=True)
        except OSError as error:
            log.warning("deletion_manifest_remove_failed", job=job_id, error=str(error))
    return report
