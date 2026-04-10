# Release Cross-Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar Bipolar Code en GitHub con ejecutables para Windows/Linux/macOS y frontend embebido en el backend.

**Architecture:** El frontend React se compila a `frontend/dist/` y FastAPI lo sirve como archivos estáticos con fallback SPA. El backend se empaqueta con PyInstaller en un único binario por plataforma. Las partes Windows-only (winreg, PowerShell) quedan protegidas por guardas `sys.platform == "win32"` con equivalentes POSIX.

**Tech Stack:** Python 3.11, FastAPI, PyInstaller 6, Node 20, GitHub Actions, `pytest-asyncio`

---

## File Map

| Archivo | Acción |
|---|---|
| `backend/app/core/config.py` | Default `LITELLM_CONFIG_DIR` por plataforma; crear dir si no existe |
| `backend/app/services/proxy_service.py` | Guardia `sys.platform` en `_set_user_env` |
| `backend/app/services/providers_service.py` | Guardia `sys.platform` en `_start_litellm` + path litellm |
| `backend/app/main.py` | Montar `StaticFiles` + fallback SPA |
| `backend/run.py` | Nuevo — entry point para PyInstaller |
| `bipolar-code.spec` | Nuevo — spec de PyInstaller (raíz del repo) |
| `.github/workflows/release.yml` | Nuevo — CI/CD release |
| `README.md` | Quick Start + Dev Setup |
| `backend/tests/test_proxy_service.py` | Actualizar tests rotos por cambio de `_set_user_env` |
| `backend/tests/test_proxy_route.py` | Actualizar test `test_enable_direct_routing_clears_env` |

---

## Task 1: Config cross-platform y creación automática del directorio

**Files:**
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: Actualizar `config.py`**

Reemplaza el contenido completo de `backend/app/core/config.py`:

```python
from pydantic_settings import BaseSettings
from functools import lru_cache
import os
import sys
from pathlib import Path

def _default_config_dir() -> str:
    if "LITELLM_CONFIG_DIR" in os.environ:
        return os.environ["LITELLM_CONFIG_DIR"]
    if sys.platform == "win32":
        return "C:/litellm"
    return str(Path.home() / ".litellm")

_DEFAULT_CONFIG_DIR = _default_config_dir()
_ENV_FILE = os.path.join(_DEFAULT_CONFIG_DIR, ".env")

# Crear el directorio si no existe (primera ejecución en Linux/Mac)
Path(_DEFAULT_CONFIG_DIR).mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    # Proxy
    proxy_url: str = "http://localhost:4001"
    proxy_api_key: str = "sk-litellm"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_real_api_key: str = ""

    # GitHub / Copilot
    github_token: str = ""
    github_oauth_token: str = ""
    copilot_session_token: str = ""

    # App
    env: str = "development"
    log_level: str = "INFO"
    litellm_config_dir: str = _DEFAULT_CONFIG_DIR

    model_config = {"env_file": _ENV_FILE, "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 2: Verificar que el backend arranca sin errores**

```bash
cd backend
python -c "from app.core.config import get_settings; s = get_settings(); print(s.litellm_config_dir)"
```

Esperado en Windows: `C:/litellm`  
Esperado en Linux/Mac: `/home/<user>/.litellm`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/config.py
git commit -m "Historia técnica 1079001: config dir cross-platform con creación automática"
```

---

## Task 2: `_set_user_env` cross-platform en `proxy_service`

**Files:**
- Modify: `backend/app/services/proxy_service.py`
- Modify: `backend/tests/test_proxy_route.py`

- [ ] **Step 1: Escribir test que pase en cualquier plataforma**

En `backend/tests/test_proxy_route.py`, reemplaza `test_enable_direct_routing_clears_env`:

```python
@pytest.mark.asyncio
async def test_enable_direct_routing_does_not_crash(monkeypatch):
    """_set_user_env no debe lanzar excepción en ninguna plataforma."""
    # Parchamos get_proxy_status para no necesitar litellm corriendo
    async def fake_status():
        return {"running": False}
    monkeypatch.setattr("app.services.proxy_service.get_proxy_status", fake_status)

    result = await proxy_service.enable_direct_routing()
    assert result["mode"] == "direct"
    assert result["applied"] is True
```

- [ ] **Step 2: Correr test para verificar estado actual**

```bash
cd backend
pytest tests/test_proxy_route.py::test_enable_direct_routing_does_not_crash -v
```

- [ ] **Step 3: Reemplazar `_set_user_env` con versión cross-platform**

En `backend/app/services/proxy_service.py`, reemplaza la función `_set_user_env` completa:

```python
def _set_user_env(key: str, value: str | None) -> None:
    """Escribe o borra una variable de entorno persistente para Claude Code.

    Windows: HKCU\\Environment (registry) + WM_SETTINGCHANGE broadcast
    Todas las plataformas: ~/.claude/settings.json sección 'env'
    """
    import sys
    import json

    # --- Windows: registry ---
    if sys.platform == "win32":
        import winreg
        import ctypes
        reg_key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        )
        try:
            if value is None:
                try:
                    winreg.DeleteValue(reg_key, key)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(reg_key, key, 0, winreg.REG_EXPAND_SZ, value)
        finally:
            winreg.CloseKey(reg_key)
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 5000, None
        )

    # --- Todas las plataformas: ~/.claude/settings.json ---
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            claude_settings = json.load(f)
        env_section = claude_settings.setdefault("env", {})
        if value is None:
            env_section.pop(key, None)
        else:
            env_section[key] = value
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(claude_settings, f, indent=2)
        log.info("claude_settings_env_written", key=key, has_value=value is not None)
    except FileNotFoundError:
        log.debug("claude_settings_not_found", path=settings_path)
    except Exception as e:
        log.warning("claude_settings_env_failed", key=key, error=str(e))

    log.info("user_env_written", key=key, has_value=value is not None)
```

- [ ] **Step 4: Correr tests**

```bash
cd backend
pytest tests/test_proxy_route.py -v
```

Esperado: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/proxy_service.py backend/tests/test_proxy_route.py
git commit -m "Historia técnica 1079002: _set_user_env cross-platform sin winreg en Linux/Mac"
```

---

## Task 3: `_start_litellm` cross-platform en `providers_service`

**Files:**
- Modify: `backend/app/services/providers_service.py`

- [ ] **Step 1: Reemplazar `_start_litellm` con versión cross-platform**

En `backend/app/services/providers_service.py`, reemplaza la función `_start_litellm` completa (líneas ~274-327):

```python
def _start_litellm(config_path: Path) -> None:
    import sys
    import os
    import copy
    import shutil
    settings = get_settings()
    env_path = Path(settings.litellm_config_dir) / ".env"

    # Cargar variables del .env en el entorno del nuevo proceso
    child_env = copy.copy(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            child_env[k.strip()] = v.strip()
    except Exception as e:
        log.warning("env_load_warning", error=str(e))

    # Resolver path del ejecutable litellm
    if sys.platform == "win32":
        fallback = Path(sys.executable).parent / "Scripts" / "litellm.exe"
    else:
        fallback = Path(sys.executable).parent / "litellm"
    litellm_exe = shutil.which("litellm") or str(fallback)
    log.info("litellm_executable", path=litellm_exe)

    out_log = Path(settings.litellm_config_dir) / "litellm-out.log"
    err_log = Path(settings.litellm_config_dir) / "litellm-err.log"

    if sys.platform == "win32":
        # PowerShell script — evita UnicodeEncodeError en consolas cp1252
        ps1_file = Path(settings.litellm_config_dir) / "_start_litellm.ps1"
        lines = ["$ErrorActionPreference = 'Stop'"]
        for k in ("PYTHONIOENCODING", "PYTHONUTF8", "COPILOT_SESSION_TOKEN",
                  "ANTHROPIC_API_KEY", "ANTHROPIC_REAL_API_KEY",
                  "GITHUB_TOKEN", "GITHUB_OAUTH_TOKEN"):
            if k in child_env:
                v = child_env[k].replace("'", "''")
                lines.append(f"$env:{k} = '{v}'")
        lines += [
            f"Start-Process '{litellm_exe}' "
            f"-ArgumentList '--config','{config_path}','--port','4001' "
            f"-WorkingDirectory '{settings.litellm_config_dir}' "
            f"-RedirectStandardOutput '{out_log}' "
            f"-RedirectStandardError '{err_log}' "
            f"-WindowStyle Hidden",
        ]
        ps1_file.write_text("\n".join(lines), encoding="utf-8")
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", str(ps1_file)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    else:
        # Linux / macOS: subprocess directo con start_new_session
        with open(out_log, "ab") as fout, open(err_log, "ab") as ferr:
            subprocess.Popen(
                [litellm_exe, "--config", str(config_path), "--port", "4001"],
                env=child_env,
                stdout=fout,
                stderr=ferr,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )

    log.info("litellm_started", config=str(config_path), out_log=str(out_log))
```

- [ ] **Step 2: Verificar que el import de `subprocess.CREATE_NO_WINDOW` no rompe Linux**

`CREATE_NO_WINDOW` solo se usa dentro del bloque `if sys.platform == "win32"`, así que no hay problema. Verificar importando el módulo:

```bash
cd backend
python -c "from app.services import providers_service; print('OK')"
```

Esperado: `OK`

- [ ] **Step 3: Correr tests de providers**

```bash
cd backend
pytest tests/test_providers_service.py -v
```

Esperado: todos PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/providers_service.py
git commit -m "Historia técnica 1079003: _start_litellm cross-platform con subprocess POSIX"
```

---

## Task 4: Frontend embebido en FastAPI

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Compilar el frontend**

```bash
cd frontend
npm ci
npm run build
```

Esperado: se crea `frontend/dist/` con `index.html` y subcarpeta `assets/`.

- [ ] **Step 2: Agregar montaje de static files en `main.py`**

En `backend/app/main.py`, al final de la función `create_app()`, antes del `return app`, agregar:

```python
    # Servir frontend compilado si existe (producción / binario PyInstaller)
    import pathlib
    dist_dir = pathlib.Path(__file__).parent.parent.parent / "frontend" / "dist"
    if dist_dir.exists():
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            return FileResponse(str(dist_dir / "index.html"))
```

- [ ] **Step 3: Instalar `aiofiles` (requerido por StaticFiles)**

```bash
cd backend
pip install aiofiles
echo "aiofiles>=23.0.0" >> requirements.txt
```

- [ ] **Step 4: Verificar que el backend sirve el frontend**

```bash
cd backend
uvicorn app.main:app --port 8000
```

Abrir `http://localhost:8000` — debe mostrar el dashboard de Bipolar Code.  
Abrir `http://localhost:8000/api/health` — debe devolver `{"status":"ok","version":"0.2.0"}`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/requirements.txt
git commit -m "Historia técnica 1079004: frontend React embebido en FastAPI como StaticFiles"
```

---

## Task 5: Entry point y spec de PyInstaller

**Files:**
- Create: `backend/run.py`
- Create: `bipolar-code.spec`

- [ ] **Step 1: Crear `backend/run.py`**

```python
"""Entry point para el ejecutable PyInstaller."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="info")
```

- [ ] **Step 2: Instalar PyInstaller**

```bash
pip install pyinstaller
```

- [ ] **Step 3: Crear `bipolar-code.spec` en la raíz del repo**

```python
# bipolar-code.spec
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Path al frontend compilado (relativo al spec)
frontend_dist = Path("frontend/dist")

a = Analysis(
    ["backend/run.py"],
    pathex=["backend"],
    binaries=[],
    datas=[
        (str(frontend_dist), "frontend/dist"),
        *collect_data_files("litellm"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "aiofiles",
        "structlog",
        "psutil",
        *collect_submodules("app"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="bipolar-code",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

- [ ] **Step 4: Probar build local**

```bash
cd c:/litellm/bipolar-code
pyinstaller bipolar-code.spec --clean
```

Esperado: crea `dist/bipolar-code.exe` (Windows) o `dist/bipolar-code` (Linux/Mac).

- [ ] **Step 5: Probar el ejecutable**

```bash
./dist/bipolar-code
```

Abrir `http://localhost:8000` — debe mostrar el dashboard.

- [ ] **Step 6: Agregar `dist/` y `build/` al `.gitignore`**

En `.gitignore` del repo, verificar que ya existan (ya están). Si no:

```
dist/
build/
*.spec.bak
```

- [ ] **Step 7: Commit**

```bash
git add backend/run.py bipolar-code.spec
git commit -m "Historia técnica 1079005: run.py y bipolar-code.spec para PyInstaller"
```

---

## Task 6: GitHub Actions release workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Crear `.github/workflows/release.yml`**

```yaml
name: Release

on:
  push:
    tags:
      - 'v*.*.*'

permissions:
  contents: write

jobs:
  build:
    strategy:
      matrix:
        include:
          - os: windows-latest
            artifact: bipolar-code-windows.exe
            binary: dist/bipolar-code.exe
          - os: ubuntu-latest
            artifact: bipolar-code-linux
            binary: dist/bipolar-code
          - os: macos-latest
            artifact: bipolar-code-macos
            binary: dist/bipolar-code

    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node 20
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json

      - name: Setup Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Build frontend
        working-directory: frontend
        run: |
          npm ci
          npm run build

      - name: Install Python dependencies
        working-directory: backend
        run: pip install -r requirements.txt pyinstaller aiofiles

      - name: Build executable
        run: pyinstaller bipolar-code.spec --clean

      - name: Rename artifact
        shell: bash
        run: mv "${{ matrix.binary }}" "${{ matrix.artifact }}"

      - name: Upload to release
        uses: softprops/action-gh-release@v2
        with:
          files: ${{ matrix.artifact }}
          generate_release_notes: true
```

- [ ] **Step 2: Crear el directorio `.github/workflows/` si no existe**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "Historia técnica 1079006: GitHub Actions workflow para release multiplataforma"
```

---

## Task 7: README actualizado

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Reemplazar el README completo**

```markdown
# Bipolar Code

Web UI para gestionar un proxy [LiteLLM](https://github.com/BerriAI/litellm) — cambia backends, explora modelos disponibles, monitorea uso y enruta Claude Code a través del proxy con un clic.

## Quick Start (ejecutable)

1. Descarga el ejecutable de la [página de Releases](../../releases) para tu plataforma:
   - `bipolar-code-windows.exe` — Windows
   - `bipolar-code-linux` — Linux
   - `bipolar-code-macos` — macOS

2. Crea el directorio de configuración:
   - Windows: `C:\litellm\`
   - Linux/macOS: `~/.litellm/`

3. Copia [`backend/.env.example`](backend/.env.example) a ese directorio como `.env` y rellena tus API keys.

4. Instala y verifica que `litellm` está disponible en el PATH:
   ```bash
   pip install litellm
   litellm --version
   ```

5. Ejecuta el binario:
   - Windows: doble clic en `bipolar-code-windows.exe`
   - Linux/macOS: `chmod +x bipolar-code-linux && ./bipolar-code-linux`

6. Abre [http://localhost:8000](http://localhost:8000)

---

## Dev Setup (código fuente)

### Requisitos

- Python 3.11+
- Node 20+
- `litellm` instalado y en PATH (`pip install litellm`)

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend (dev)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # vitest
```

### Tests backend

```bash
cd backend
pytest
```

---

## Configuración

El backend lee el archivo `.env` desde el directorio de configuración:

| Plataforma | Ruta por defecto |
|---|---|
| Windows | `C:\litellm\.env` |
| Linux/macOS | `~/.litellm/.env` |

Puede sobreridarse con la variable de entorno `LITELLM_CONFIG_DIR`.

Ver [`backend/.env.example`](backend/.env.example) para todas las variables disponibles.

---

## Stack

- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS + TanStack Query
- **Backend**: Python FastAPI + structlog + httpx + pydantic-settings

## Features

- Estado del proxy en tiempo real con indicador de backend activo
- Cambio de proveedor con un clic (Copilot / Claude / LM Studio / cualquier OpenAI-compatible)
- Chat con streaming y soporte de visión (imágenes)
- Auto-refresco del token de GitHub Copilot en background
- Enrutamiento de Claude Code: cambia entre Anthropic directo y LiteLLM proxy
- Logs de uso y costo estimado

## License

MIT
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Historia técnica 1079007: README con Quick Start para release y Dev Setup"
```

---

## Task 8: Push y crear tag de release

- [ ] **Step 1: Push de todos los commits**

```bash
git push origin master
```

- [ ] **Step 2: Crear tag v1.0.0**

```bash
git tag v1.0.0
git push origin v1.0.0
```

Esto dispara el workflow de GitHub Actions. Verificar en `https://github.com/santiquiroz/bipolar-code/actions` que los 3 jobs (Windows, Linux, macOS) completan.

- [ ] **Step 3: Verificar el release**

Ir a `https://github.com/santiquiroz/bipolar-code/releases` — debe aparecer `v1.0.0` con los 3 artefactos adjuntos.
