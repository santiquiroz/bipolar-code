# Release cross-platform de Bipolar Code

**Fecha:** 2026-04-10  
**Estado:** Aprobado

## Objetivo

Publicar Bipolar Code en GitHub con:
- Ejecutables descargables para Windows, Linux y macOS (PyInstaller + GitHub Actions)
- Frontend React embebido en el binario del backend (sin servidor Vite separado)
- Código fuente con instrucciones de setup para devs
- Backend completamente cross-platform (sin código Windows-only sin fallback)

---

## 1. Cross-platform backend

### 1.1 `proxy_service._set_user_env`

Actualmente usa `winreg` y `ctypes.windll` sin guardia de plataforma. Queda roto en Linux/Mac.

**Cambio:** Condicional por `sys.platform`:

```python
if sys.platform == "win32":
    # winreg + WM_SETTINGCHANGE broadcast
else:
    pass  # no hay registry; solo settings.json de Claude Code
# Siempre: escribir ~/.claude/settings.json
```

### 1.2 `providers_service._start_litellm`

Actualmente genera un `.ps1` y lo ejecuta con `powershell.exe`. Falla en Linux/Mac.

**Cambio:**

```python
if sys.platform == "win32":
    # comportamiento actual: PowerShell script
else:
    # subprocess.Popen con shell=False, stdout/stderr redirigidos a log files
    # start_new_session=True para desacoplar el proceso del padre
```

### 1.3 Path del ejecutable litellm

```python
if sys.platform == "win32":
    fallback = Path(sys.executable).parent / "Scripts" / "litellm.exe"
else:
    fallback = Path(sys.executable).parent / "litellm"
```

### 1.4 `config.py` — `LITELLM_CONFIG_DIR` default

```python
if sys.platform == "win32":
    _DEFAULT = "C:/litellm"
else:
    _DEFAULT = str(Path.home() / ".litellm")
```

El directorio se crea automáticamente si no existe al arrancar.

---

## 2. Frontend embebido en FastAPI

### 2.1 Build

```bash
cd frontend && npm ci && npm run build
# genera frontend/dist/
```

### 2.2 Montaje en FastAPI

En `main.py`, después de los routers:

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import pathlib

DIST = pathlib.Path(__file__).parent.parent.parent / "frontend" / "dist"

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return FileResponse(DIST / "index.html")
```

El frontend usa `/api/*` para todo — sin cambios en las URLs.

En dev, Vite sigue corriendo en 5173 con su proxy a 8000 como antes.

---

## 3. PyInstaller

### 3.1 Spec file: `bipolar-code.spec`

- Entry point: `backend/app/main.py` (via wrapper `run.py`)
- `datas`: incluye `frontend/dist/` en el bundle
- `hiddenimports`: `winreg` (solo Windows), `uvicorn`, `structlog`
- `onefile=True`, `console=False` (Windows), `console=True` (Linux/Mac para ver logs)

### 3.2 `run.py` (wrapper en raíz del backend)

```python
import uvicorn
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
```

### 3.3 Comportamiento al ejecutar

1. Inicia uvicorn en puerto 8000
2. Imprime (o abre automáticamente) `http://localhost:8000`
3. El usuario interactúa desde el browser

---

## 4. GitHub Actions

### Archivo: `.github/workflows/release.yml`

**Trigger:** `push` de tag `v*.*.*`

**Jobs:** matriz en 3 runners: `windows-latest`, `ubuntu-latest`, `macos-latest`

**Steps por job:**
1. Checkout
2. Setup Python 3.11 + Node 20
3. `npm ci && npm run build` en `frontend/`
4. `pip install -r requirements.txt pyinstaller` en `backend/`
5. `pyinstaller bipolar-code.spec`
6. Upload artifact al GitHub Release

**Nombres de artefactos:**
- `bipolar-code-windows.exe`
- `bipolar-code-linux`
- `bipolar-code-macos`

---

## 5. README actualizado

### Sección Quick Start (release)

```
1. Descarga el ejecutable de Releases para tu plataforma
2. Crea el directorio de configuración:
   - Windows: C:\litellm\
   - Linux/Mac: ~/.litellm/
3. Copia .env.example a ese directorio como .env y rellena tus keys
4. Ejecuta bipolar-code (Windows: doble clic; Linux/Mac: ./bipolar-code)
5. Abre http://localhost:8000
```

### Sección Dev Setup (fuente)

Instrucciones actuales de backend (uvicorn) + frontend (vite) sin cambios.

---

## Archivos a crear/modificar

| Archivo | Acción |
|---|---|
| `backend/app/services/proxy_service.py` | Guardia `sys.platform` en `_set_user_env` |
| `backend/app/services/providers_service.py` | Guardia `sys.platform` en `_start_litellm` + path litellm |
| `backend/app/core/config.py` | Default `LITELLM_CONFIG_DIR` por plataforma + crear dir si no existe |
| `backend/app/main.py` | Montar `StaticFiles` + fallback SPA cuando `dist/` existe |
| `backend/run.py` | Nuevo — wrapper para PyInstaller |
| `bipolar-code.spec` | Nuevo — spec de PyInstaller en raíz del repo |
| `.github/workflows/release.yml` | Nuevo — CI/CD para release |
| `README.md` | Quick Start + Dev Setup |
