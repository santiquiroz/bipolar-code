"""Pruebas del manifiesto seguro de borrado por job."""
import shutil
import subprocess

import pytest

from app.services.cli_agents.deletions import MANIFEST_NAME, apply_deletions

GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not GIT_AVAILABLE, reason="git no disponible")


def _git(*args, cwd):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _repo(root):
    _git("init", cwd=root)
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b").mkdir()
    (root / "b" / "c.txt").write_text("c", encoding="utf-8")
    _git("add", "a.txt", "b/c.txt", cwd=root)
    _git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial", cwd=root)


def _manifest(workspace, content, job_id="job"):
    path = workspace / ".bipolar" / "jobs" / job_id / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@requires_git
def test_borra_archivos_versionados_y_elimina_manifiesto(tmp_path):
    _repo(tmp_path)
    manifest = _manifest(tmp_path, '"a.txt"\nb\\c.txt\n')

    report = apply_deletions(tmp_path, "job", set())

    assert report.deleted == ["a.txt", "b/c.txt"]
    assert report.rejected == []
    assert not (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b" / "c.txt").exists()
    assert not manifest.exists()


@requires_git
@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ("../fuera.txt", "contiene segmento .."),
        (None, "ruta absoluta"),
        (".git/config", "ruta protegida"),
        (".bipolar/x", "ruta protegida"),
        ("carpeta", "no es un archivo regular"),
        ("inexistente.txt", "no existe"),
        ("existente-sin-seguimiento.txt", "sin respaldo en git"),
    ],
)
def test_rechaza_rutas_no_permitidas(tmp_path, entry, reason):
    _repo(tmp_path)
    (tmp_path / "carpeta").mkdir()
    (tmp_path / "existente-sin-seguimiento.txt").write_text("old", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    if entry is None:
        entry = str(outside)
    manifest = _manifest(tmp_path, entry + "\n")

    report = apply_deletions(tmp_path, "job", set())

    assert report.deleted == []
    assert report.rejected == [(entry.replace("\\", "/"), reason)]
    assert not manifest.exists()


@requires_git
def test_borra_archivo_sin_seguimiento_creado_durante_job(tmp_path):
    _repo(tmp_path)
    created = tmp_path / "new.txt"
    created.write_text("new", encoding="utf-8")
    _manifest(tmp_path, "new.txt\n")

    report = apply_deletions(tmp_path, "job", {"new.txt"})

    assert report.deleted == ["new.txt"] and report.rejected == []
    assert not created.exists()


@requires_git
def test_rechaza_symlink(tmp_path):
    _repo(tmp_path)
    link = tmp_path / "enlace.txt"
    try:
        link.symlink_to(tmp_path / "a.txt")
    except OSError as error:
        pytest.skip(f"symlink no disponible: {error}")
    _manifest(tmp_path, "enlace.txt\n")

    report = apply_deletions(tmp_path, "job", set())

    assert report.deleted == []
    assert report.rejected == [("enlace.txt", "es un symlink")]
    assert link.is_symlink()


def test_workspace_sin_git_solo_borra_archivo_creado_en_job(tmp_path):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    _manifest(tmp_path, "old.txt\nnew.txt\n")

    report = apply_deletions(tmp_path, "job", {"new.txt"})

    assert report.deleted == ["new.txt"]
    assert report.rejected == [("old.txt", "sin respaldo en git")]
    assert old.exists() and not new.exists()


@requires_git
def test_no_borra_archivo_sin_seguimiento_existente_en_repo_gitado(tmp_path):
    _repo(tmp_path)
    old = tmp_path / "old.txt"
    old.write_text("old", encoding="utf-8")
    _manifest(tmp_path, "old.txt\n")

    report = apply_deletions(tmp_path, "job", set())

    assert report.rejected == [("old.txt", "sin respaldo en git")]
    assert old.exists()
