# Plan C — Lanzador `bipolar-claude`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `bipolar-claude` abra Claude Code interactivo con la suscripción que tenga cupo según bipolar, que caiga en modo proxy (bipolar como `ANTHROPIC_BASE_URL`) cuando no queda ninguna, y que la carpeta de cada cuenta tenga los plugins, skills, CLAUDE.md y servidores MCP del usuario.

**Architecture:** Un script Python solo-stdlib (`scripts/bipolar_claude.py`) con funciones puras: elegir el modo, transformar el `settings.json` y planificar el espejo. Hay un núcleo de efectos chico: junctions, copias y lanzar `claude`. Los wrappers `bipolar-claude.cmd` y `bipolar-claude.sh` solo invocan Python. La decisión de cuenta la toma bipolar (`GET /api/accounts/pick`, plan B).

**Tech Stack:** Python 3.11 stdlib (`urllib`, `json`, `subprocess`, `shutil`, `signal`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` (sección 8). Cambio respecto de la spec: el `-Continue` copia el transcript y usa `claude --resume <id de sesión>`, porque en la versión 2.1.293 `--resume` recibe un id, no una ruta. La spec se ajusta en la Task C2.

**Depende de:** Plan B (B4 y B5: `/api/accounts/pick` y `scripts/bipolar-statusline.py`).

## Global Constraints

- Solo stdlib. Funciona en Windows (junctions con `mklink /J`, sin admin) y en POSIX (`os.symlink`).
- Flags propios con prefijo `--bc-` (`--bc-continue`, `--bc-no-mirror`, `--bc-dry-run`); el resto de argumentos pasa intacto a `claude`.
- El espejo nunca destruye nada en la carpeta de la cuenta:
  - Si el destino de una junction ya existe y no es un link, se deja y se avisa.
  - Nunca se copian `.credentials.json`, `projects/`, `history.jsonl` ni `.claude.json` completo; de este último solo se fusiona `mcpServers`.
- Si bipolar no responde en 3 s, se lanza `claude` tal cual, sin variables nuevas.
- Mientras corre `claude`, el lanzador ignora Ctrl+C (lo atiende Claude Code) y devuelve el código de salida del hijo.
- No ejecutar `git add/commit/push/reset/checkout`: el orquestador commitea.

## Review Focus

1. **Carpeta de cuenta con un directorio real donde iría una junction** (por ejemplo, el usuario creó `skills/` a mano): no se borra ni se reemplaza; se avisa. Test en C1.
2. **`settings.json` del usuario con `statusLine` propio y `env` con `ANTHROPIC_BASE_URL`**: la copia de la cuenta no tiene las variables de proxy, y el reporter de bipolar envuelve el `statusLine` original con `--then`. Test en C1.
3. **bipolar caído o la llave equivocada** (401): el lanzador abre `claude` normal, sin quedarse colgado. Test en C2.
4. **`--bc-continue` sin transcript previo para este directorio**: abre sesión nueva y avisa, sin pasar un `--resume` roto. Test en C2.
5. **Ruta de trabajo con espacios o tildes**: el slug de `projects/` coincide con el que usa Claude Code (cada carácter no alfanumérico pasa a `-`). Test en C1.

---

### Task C1: Núcleo puro del lanzador

**Files:**
- Create: `scripts/bipolar_claude.py`
- Test: `backend/tests/test_bipolar_claude_launcher.py`

**Interfaces** (funciones de módulo; el test carga el script con `importlib`, igual que `test_statusline_reporter.py`):

```python
MIRROR_DIRS = ("skills", "agents", "commands", "rules", "hooks", "plugins")
PROXY_ENV_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
DEFAULT_URL = "http://127.0.0.1:8000"

def default_config_dir(environ: Mapping[str, str]) -> Path
def read_api_key(config_dir: Path, environ: Mapping[str, str]) -> str
def project_slug(cwd: Path) -> str                         # re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
def statusline_command(python_exe: str, reporter: Path, agent_id: str, original: Optional[str]) -> str
def mirrored_settings(user_settings: dict, reporter_cmd: str) -> dict
def merge_mcp_servers(account_json: dict, user_json: dict) -> dict
def mirror_plan(user_dir: Path, account_dir: Path) -> list[tuple[str, Path, Path]]
def latest_transcript(config_dir: Path, cwd: Path) -> Optional[Path]
```

**Reglas:**
- `default_config_dir`: `LITELLM_CONFIG_DIR` si está; si no, `C:/litellm` en Windows y `~/.litellm` en otros.
- `read_api_key`: `BIPOLAR_API_KEY` del entorno; si no, la línea `UI_API_KEY=` del `.env` del config dir (sin comillas); si no, `""`.
- `statusline_command`: `f'"{python_exe}" "{reporter}" --agent-id {agent_id}'`, más ` --then "<original con comillas dobles escapadas como \\\">"` si hay original.
- `mirrored_settings`:
  - Copia profunda (`json.loads(json.dumps(...))`).
  - Quita del dict `env` (si existe) las claves de `PROXY_ENV_KEYS`; si `env` queda vacío, se elimina.
  - `statusLine` pasa a `{"type": "command", "command": reporter_cmd}`. El llamador ya armó `reporter_cmd` con el `--then` del original.
  - No toca nada más.
- `merge_mcp_servers`: devuelve una copia de `account_json` con `mcpServers` = los del usuario, actualizados con los de la cuenta. Si la cuenta tiene uno con el mismo nombre, gana el de la cuenta.
- `mirror_plan` (puro: solo lee el disco):
  - Por cada `d` de `MIRROR_DIRS` que exista en `user_dir`:
    - Si `account_dir/d` no existe → `("link", user_dir/d, account_dir/d)`.
    - Si existe y es un link o junction → nada. Detectarlo con `os.path.islink` o, en Windows, con `Path.is_junction()` (Python 3.12+); en 3.11, comparar `os.path.realpath(dst) != os.path.abspath(dst)`.
    - Si existe y es un directorio real → `("skip", user_dir/d, account_dir/d)`.
  - Si `user_dir/CLAUDE.md` existe → `("copy", src, dst)`.
- `latest_transcript`: el `*.jsonl` más reciente (por `st_mtime`) en `config_dir/projects/<slug>/`, comparando el nombre de la carpeta sin distinguir mayúsculas. `None` si no hay.

- [ ] **Step 1: Escribir los tests**

```python
"""Lanzador bipolar-claude: núcleo puro (espejo, settings, slug, transcript)."""
import importlib.util
import json
import os
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bipolar_claude.py"


def _load():
    spec = importlib.util.spec_from_file_location("bipolar_claude", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_slug_matches_claude_code():
    bc = _load()
    assert bc.project_slug(Path(r"C:\Users\santi\.claude-mem\observer sessions")) == "C--Users-santi--claude-mem-observer-sessions"
    assert bc.project_slug(Path(r"c:\personal\bipolar-code\bipolar-code")) == "c--personal-bipolar-code-bipolar-code"


def test_mirrored_settings_strips_proxy_and_wraps_statusline():
    bc = _load()
    user = {"env": {"ANTHROPIC_BASE_URL": "http://x", "ANTHROPIC_API_KEY": "k", "FOO": "1"},
            "statusLine": {"type": "command", "command": "node mine.js"}, "theme": "dark"}
    cmd = bc.statusline_command("python", Path("/r/bipolar-statusline.py"), "claude-2", user["statusLine"]["command"])
    out = bc.mirrored_settings(user, cmd)
    assert out["env"] == {"FOO": "1"}
    assert out["statusLine"] == {"type": "command", "command": cmd}
    assert "--agent-id claude-2" in cmd and '--then "node mine.js"' in cmd
    assert out["theme"] == "dark"
    assert user["env"]["ANTHROPIC_BASE_URL"] == "http://x"


def test_mirrored_settings_drops_empty_env():
    bc = _load()
    out = bc.mirrored_settings({"env": {"ANTHROPIC_BASE_URL": "http://x"}}, "cmd")
    assert "env" not in out


def test_merge_mcp_servers_account_wins():
    bc = _load()
    merged = bc.merge_mcp_servers({"mcpServers": {"a": {"url": "acct"}}, "other": 1},
                                  {"mcpServers": {"a": {"url": "user"}, "b": {"url": "u2"}}, "secret": "x"})
    assert merged["mcpServers"] == {"a": {"url": "acct"}, "b": {"url": "u2"}}
    assert merged["other"] == 1 and "secret" not in merged


def test_mirror_plan_links_missing_and_skips_real_dirs(tmp_path):
    bc = _load()
    user, acct = tmp_path / "user", tmp_path / "acct"
    for d in ("skills", "plugins", "agents"):
        (user / d).mkdir(parents=True)
    (user / "CLAUDE.md").write_text("reglas", encoding="utf-8")
    (acct / "agents").mkdir(parents=True)
    plan = bc.mirror_plan(user, acct)
    kinds = {(k, dst.name) for k, _, dst in plan}
    assert ("link", "skills") in kinds and ("link", "plugins") in kinds
    assert ("skip", "agents") in kinds
    assert ("copy", "CLAUDE.md") in kinds


def test_latest_transcript_case_insensitive(tmp_path):
    bc = _load()
    cwd = Path(r"C:\work\repo")
    folder = tmp_path / "projects" / bc.project_slug(cwd).lower()
    folder.mkdir(parents=True)
    old, new = folder / "a.jsonl", folder / "b.jsonl"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    os.utime(old, (1, 1))
    assert bc.latest_transcript(tmp_path, cwd) == new
    assert bc.latest_transcript(tmp_path, Path(r"C:\otro")) is None


def test_read_api_key_env_then_dotenv(tmp_path):
    bc = _load()
    (tmp_path / ".env").write_text('UI_API_KEY="bc-file"\n', encoding="utf-8")
    assert bc.read_api_key(tmp_path, {"BIPOLAR_API_KEY": "bc-env"}) == "bc-env"
    assert bc.read_api_key(tmp_path, {}) == "bc-file"
    assert bc.read_api_key(tmp_path / "nope", {}) == ""
```

- [ ] **Step 2: Correr y verificar que fallan.**
- [ ] **Step 3: Implementar el núcleo puro** en `scripts/bipolar_claude.py`, todavía sin `main`.
- [ ] **Step 4: Correr los tests** → verde.

---

### Task C2: Efectos y `main`

**Files:**
- Modify: `scripts/bipolar_claude.py`
- Create: `scripts/bipolar-claude.cmd`, `scripts/bipolar-claude.sh`
- Modify: `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` §8, paso 4 (`--resume <id>` con copia del transcript)
- Test: `backend/tests/test_bipolar_claude_launcher.py` (agregar)

**Interfaces:**

```python
def fetch_pick(url: str, key: str, timeout: float = 3.0, opener=urllib.request.urlopen) -> Optional[dict]
def apply_mirror(user_dir: Path, account_dir: Path, settings_cmd: str, link=None) -> list[str]   # devuelve avisos
def make_link(src: Path, dst: Path) -> None
def continue_session(prev_dir: Path, new_dir: Path, cwd: Path) -> Optional[str]   # id de sesión copiado o None
def launch_plan(pick: Optional[dict], key: str, base_url: str) -> tuple[dict, str]  # (variables nuevas, modo: account|proxy|plain)
def main(argv: list[str], environ: Mapping[str, str], run=subprocess.call, opener=urllib.request.urlopen, out=sys.stderr) -> int
```

**Reglas:**
- `fetch_pick`: GET `{url}/api/accounts/pick?adapter=claude` con header `x-api-key`. Cualquier error (red, timeout, status ≥ 400, JSON inválido) → `None`.
- `launch_plan`:
  - `pick is None` → `({}, "plain")`.
  - `mode == "account"` → `({"CLAUDE_CONFIG_DIR": pick["account_dir"]}, "account")`.
  - `mode == "proxy"` → `({"ANTHROPIC_BASE_URL": pick.get("base_url") or base_url, "ANTHROPIC_API_KEY": key}, "proxy")`.
- `apply_mirror`:
  - Ejecuta `mirror_plan`: `link` → `make_link`; `copy` → `shutil.copy2`; `skip` → un aviso.
  - Escribe `account_dir/settings.json` con `mirrored_settings(user_settings, settings_cmd)`. Si el usuario no tiene `settings.json`, parte de `{}`.
  - Fusiona `mcpServers` del `.claude.json` del usuario (en `Path.home() / ".claude.json"` cuando el usuario no usa `CLAUDE_CONFIG_DIR`) dentro de `account_dir/.claude.json`, creándolo si no existe. Verificado el 2026-10-07: con `CLAUDE_CONFIG_DIR`, Claude Code guarda `.claude.json` dentro de esa carpeta.
- `make_link`: en Windows, `subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], check=True, capture_output=True)`; en otros, `os.symlink(src, dst, target_is_directory=True)`.
- `continue_session`:
  - `latest_transcript(prev_dir, cwd)`; si no hay → `None`.
  - Si hay, lo copia a `new_dir/projects/<slug>/<mismo nombre>` y devuelve el `stem`, que es el id de sesión.
- `main`:
  1. Separar los flags `--bc-*` del resto.
  2. `config_dir = default_config_dir(environ)`, `key = read_api_key(...)`, `url = environ.get("BIPOLAR_URL", DEFAULT_URL)`.
  3. `pick = fetch_pick(url, key)` y `env_updates, mode = launch_plan(pick, key, url)`.
  4. Modo `account` sin `--bc-no-mirror`: `apply_mirror(Path.home() / ".claude", Path(pick["account_dir"]), statusline_command(sys.executable, <ruta de bipolar-statusline.py junto a este script>, pick["agent_id"], <statusLine original del usuario o None>))`. Imprimir los avisos en `out`.
  5. Con `--bc-continue`:
     - Leer `config_dir/accounts/.last` (JSON `{"agent_id", "account_dir"}`). La carpeta previa es `account_dir` del `.last` o, si fue modo proxy o plain, `Path.home() / ".claude"`.
     - La nueva es `pick["account_dir"]` o `Path.home() / ".claude"`.
     - Si son distintas, `continue_session(prev, new, Path.cwd())`. Con id: agregar `["--resume", id]` a los argumentos de claude. Sin id: aviso "no hay sesión previa para este directorio; se abre una nueva".
  6. Escribir `.last` con la cuenta actual: `agent_id` vacío en modo proxy o plain.
  7. `--bc-dry-run`: imprimir en `out` un JSON `{"mode", "env": <claves de env_updates sin el valor de ANTHROPIC_API_KEY>, "args"}` y devolver 0 sin lanzar.
  8. Lanzar: `env = {**environ, **env_updates}`. En modo `account`, quitar del env `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` y `ANTHROPIC_API_KEY` heredados, para que la sesión use la suscripción. Ignorar SIGINT mientras corre `run(["claude", *claude_args], env=env)` y restaurarlo después. Devolver su código.
- `bipolar-claude.cmd`: `@python "%~dp0bipolar_claude.py" %*`
- `bipolar-claude.sh`: `#!/usr/bin/env sh` + `exec python3 "$(dirname "$0")/bipolar_claude.py" "$@"` (con `chmod +x`).

- [ ] **Step 1: Agregar los tests**

```python
import io


def _pick_opener(payload=None, status=200, exc=None):
    class Resp:
        def __init__(self):
            self.status = status
        def read(self):
            return json.dumps(payload).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        if exc:
            raise exc
        return Resp()
    return opener


def test_fetch_pick_failures_are_none():
    bc = _load()
    assert bc.fetch_pick("http://x", "k", opener=_pick_opener(exc=OSError("down"))) is None
    assert bc.fetch_pick("http://x", "k", opener=_pick_opener({"mode": "proxy"}, status=401)) is None
    assert bc.fetch_pick("http://x", "k", opener=_pick_opener({"mode": "proxy"})) == {"mode": "proxy"}


def test_launch_plan_modes():
    bc = _load()
    assert bc.launch_plan(None, "k", "http://b") == ({}, "plain")
    assert bc.launch_plan({"mode": "account", "account_dir": "C:/a", "agent_id": "claude-2"}, "k", "http://b") == ({"CLAUDE_CONFIG_DIR": "C:/a"}, "account")
    env, mode = bc.launch_plan({"mode": "proxy"}, "k", "http://b")
    assert mode == "proxy" and env == {"ANTHROPIC_BASE_URL": "http://b", "ANTHROPIC_API_KEY": "k"}


def test_apply_mirror_never_overwrites_real_dir(tmp_path, monkeypatch):
    bc = _load()
    user, acct = tmp_path / "user", tmp_path / "acct"
    (user / "skills").mkdir(parents=True)
    (user / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://x"}}), encoding="utf-8")
    (acct / "skills").mkdir(parents=True)
    (acct / "skills" / "mine.md").write_text("propio", encoding="utf-8")
    links = []
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    warnings = bc.apply_mirror(user, acct, "cmd", link=lambda s, d: links.append((s, d)))
    assert (acct / "skills" / "mine.md").read_text(encoding="utf-8") == "propio"
    assert any("skills" in w for w in warnings) and links == []
    settings = json.loads((acct / "settings.json").read_text(encoding="utf-8"))
    assert "env" not in settings and settings["statusLine"]["command"] == "cmd"


def test_main_dry_run_account_mode(tmp_path, monkeypatch):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-2"
    acct.mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    out = io.StringIO()
    opener = _pick_opener({"mode": "account", "agent_id": "claude-2", "account_dir": str(acct)})
    rc = bc.main(["--bc-dry-run", "--bc-no-mirror", "-p", "hola"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
                 run=lambda *a, **k: 99, opener=opener, out=out)
    assert rc == 0
    report = json.loads(out.getvalue().strip().splitlines()[-1])
    assert report["mode"] == "account" and report["args"] == ["-p", "hola"] and "CLAUDE_CONFIG_DIR" in report["env"]


def test_main_plain_when_bipolar_down_and_passes_exit_code(tmp_path, monkeypatch):
    bc = _load()
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    seen = {}

    def run(argv, env=None):
        seen["argv"], seen["env"] = argv, env
        return 7

    rc = bc.main(["--version"], {"LITELLM_CONFIG_DIR": str(tmp_path), "ANTHROPIC_BASE_URL": "http://keep"}, run=run,
                 opener=_pick_opener(exc=OSError("down")), out=io.StringIO())
    assert rc == 7 and seen["argv"] == ["claude", "--version"]
    assert seen["env"]["ANTHROPIC_BASE_URL"] == "http://keep"


def test_main_account_mode_drops_inherited_proxy_vars(tmp_path, monkeypatch):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-2"
    acct.mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    seen = {}
    bc.main(["--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "ANTHROPIC_BASE_URL": "http://proxy", "BIPOLAR_API_KEY": "k"},
            run=lambda argv, env=None: seen.setdefault("env", env) and 0,
            opener=_pick_opener({"mode": "account", "agent_id": "claude-2", "account_dir": str(acct)}), out=io.StringIO())
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(acct) and "ANTHROPIC_BASE_URL" not in seen["env"]


def test_continue_without_previous_session_warns(tmp_path, monkeypatch):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-3"
    acct.mkdir(parents=True)
    (tmp_path / "accounts" / ".last").write_text(json.dumps({"agent_id": "claude-2", "account_dir": str(tmp_path / "accounts" / "claude-2")}), encoding="utf-8")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    out, seen = io.StringIO(), {}
    bc.main(["--bc-continue", "--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
            run=lambda argv, env=None: seen.setdefault("argv", argv) and 0,
            opener=_pick_opener({"mode": "account", "agent_id": "claude-3", "account_dir": str(acct)}), out=out)
    assert "--resume" not in seen["argv"] and "nueva" in out.getvalue()


def test_continue_copies_transcript_and_resumes(tmp_path, monkeypatch):
    bc = _load()
    prev, new = tmp_path / "accounts" / "claude-2", tmp_path / "accounts" / "claude-3"
    new.mkdir(parents=True)
    folder = prev / "projects" / bc.project_slug(Path.cwd())
    folder.mkdir(parents=True)
    (folder / "sess-123.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (tmp_path / "accounts" / ".last").write_text(json.dumps({"agent_id": "claude-2", "account_dir": str(prev)}), encoding="utf-8")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    seen = {}
    bc.main(["--bc-continue", "--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
            run=lambda argv, env=None: seen.setdefault("argv", argv) and 0,
            opener=_pick_opener({"mode": "account", "agent_id": "claude-3", "account_dir": str(new)}), out=io.StringIO())
    assert seen["argv"][-2:] == ["--resume", "sess-123"]
    assert (new / "projects" / bc.project_slug(Path.cwd()) / "sess-123.jsonl").exists()
```

- [ ] **Step 2: Correr y verificar que fallan.**
- [ ] **Step 3: Implementar** los efectos, `main` y los wrappers, y ajustar la spec.
- [ ] **Step 4: Correr los tests** → verde.
- [ ] **Step 5: Probar de verdad** (lección del proyecto: un script que no se ejecuta llega roto):

```powershell
python scripts/bipolar_claude.py --bc-dry-run -p "hola"
scripts\bipolar-claude.cmd --bc-dry-run --version
```

Expected: imprime un JSON con `mode` (`plain` si bipolar no corre en `BIPOLAR_URL`, o el modo real si corre) y sale con 0.
