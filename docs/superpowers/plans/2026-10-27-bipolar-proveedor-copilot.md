# Bipolar como proveedor de Copilot Chat — plan de implementación

> **Para quien ejecute:** cada tarea se delega con un contrato propio por `mcp__bipolar__delegate(agent="anthropic", verify=[…])`, en piezas pequeñas porque el carril corta en ~$1 por job. El orquestador revisa el diff contra esta tarea, corre la verificación y hace el commit. Los pasos usan casillas (`- [ ]`).

**Objetivo:** que Copilot Chat muestre a **Bipolar** como proveedor, con su catálogo de modelos, la posibilidad de fijar un modelo y la opción **Bipolar Auto**.

**Arquitectura:**
- Backend:
  - catálogo nuevo `GET /v1/bipolar/models`;
  - en `/v1/messages`, un destino fijado por el encabezado `X-Bipolar-Provider` y el modelo especial `bipolar-auto`;
  - el resto del ruteo no se toca.
- Extensión TypeScript en `integrations/vscode-copilot/` con vendor `bipolar`:
  - pide el catálogo a bipolar;
  - convierte los mensajes de VS Code a Anthropic Messages;
  - lee la respuesta en streaming (SSE).

**Stack:** FastAPI + pytest (backend); TypeScript 5.8 + API `vscode.lm` (≥ 1.127) + `node:test` (extensión), sin dependencias de runtime.

**Spec:** `docs/superpowers/specs/2026-10-27-bipolar-proveedor-copilot-design.md`

## Restricciones globales

- **Nombres exactos:**
  - encabezado `X-Bipolar-Provider` (en el código se lee como `x-bipolar-provider`);
  - modelo Auto `bipolar-auto`, que se muestra como «Bipolar Auto»;
  - catálogo en `GET /v1/bipolar/models`;
  - vendor `bipolar`, que se muestra como «Bipolar».
- **Valores por defecto:**
  - `DEFAULT_MAX_INPUT = 128000`;
  - `DEFAULT_MAX_OUTPUT = 16000`;
  - caché del catálogo `CACHE_TTL_S = 300`.
- **Lo que no cambia:**
  - una petición a `/v1/messages` sin `X-Bipolar-Provider` y con un `model` distinto de `bipolar-auto` sigue exactamente el camino de antes;
  - `GET /v1/models`, `/v1/chat/completions`, el broker, el MCP, los agentes CLI, el provider activo y las reglas quedan intactos.
- **Destino fijado:**
  - no lee ni escribe `smart_router._sticky`;
  - respeta el presupuesto;
  - no hace failover a otros providers;
  - errores: 400 si el provider no existe, 400 si no tiene credencial y no es local, 429 si agotó el presupuesto. Todos con cuerpo `upstream.anthropic_error_body`.
- **Estilo de código (reglas del usuario):**
  - funciones atómicas, sin docstrings ni comentarios de documentación; solo un comentario de una línea cuando el porqué no sea obvio;
  - mensajes al usuario en español.
- **Commits:** en español con el estilo del historial (`<Tipo>: <descripción>`), sin la línea `Co-Authored-By`. Rama `feature/proveedor-copilot`.
- **Extensión:**
  - `engines.vscode` `^1.127.0`;
  - devDependencies: `@types/vscode` `^1.120.0`, `typescript` `^5.8.0`, `@types/node` `^22.0.0`;
  - `fetch` nativo de Node; licencia MIT, la del repo.

## Lo que más puede fallar (Review Focus)

1. **Prompt de Copilot en modo agente de más de 8192 tokens**, con modelo fijado o Auto: debe llegar entero al upstream, sin truncar. Test en la Tarea 3.
2. **Provider fijado caído**: el usuario ve un error y la petición no va en silencio a otro provider. Test en la Tarea 3.
3. **Auto cuando el provider elegido no tiene `active_model`**: 400 claro, nunca reenviar `bipolar-auto`. Test en la Tarea 3.
4. **Un `models_endpoint` que falla o tarda**: el catálogo sigue con los demás providers y deja el `active_model` del que falló. Test en la Tarea 1.
5. **Tool call cuyo `partial_json` llega partido en muchos fragmentos**, o dos tool calls intercaladas: Copilot recibe cada llamada completa una sola vez. Test en la Tarea 5.

---

### Tarea 1: Catálogo de modelos (servicio + endpoint)

**Archivos:**
- Modificar: `backend/app/services/providers_service.py` (agregar `is_chat_model` y `fetch_model_items`)
- Modificar: `backend/app/api/providers.py` (mover `_NON_CHAT_PATTERNS` y `_is_chat_model` al servicio; `list_provider_models` usa `fetch_model_items` y conserva su forma de respuesta y su mapeo de errores)
- Crear: `backend/app/services/copilot_catalog.py`
- Crear: `backend/app/api/copilot.py` (router sin prefijo)
- Modificar: `backend/app/main.py` (registrar el router junto a `openai_compat_router`)
- Test: `backend/tests/test_copilot_catalog.py`

**Interfaces:**
- Produce, en `providers_service`:
  - `is_chat_model(model_id: str) -> bool`;
  - `async fetch_model_items(provider: Provider) -> list[dict]`. Hace `GET models_endpoint` con `Bearer` (de `models_auth_env_var` o `auth_env_var`) más `extra_headers` y un timeout de 10 s. Normaliza la respuesta, sea una lista, `data` o `models`. Garantiza la clave `"id"` (si falta, usa `name`), deduplica y filtra con `is_chat_model`. Las excepciones de `httpx` se propagan.
- Produce, en `copilot_catalog`:
  - constantes `AUTO_MODEL_ID = "bipolar-auto"`, `AUTO_MODEL_NAME = "Bipolar Auto"`, `DEFAULT_MAX_INPUT = 128000`, `DEFAULT_MAX_OUTPUT = 16000`, `CACHE_TTL_S = 300`;
  - `model_limits(item: dict) -> tuple[int, int, bool | None, bool | None]`: devuelve `(max_input, max_output, tools, vision)`, con 0 o `None` cuando no se sabe;
  - `catalog_entry(provider: Provider, model: str, item: dict | None) -> dict`;
  - `keep_model(provider: Provider, model_id: str) -> bool`;
  - `async is_usable(provider: Provider) -> bool`;
  - `async provider_entries(provider: Provider) -> list[dict]`;
  - `async build_catalog() -> list[dict]`;
  - `auto_entry() -> dict`;
  - `clear_cache() -> None`.
- Produce: `GET /v1/bipolar/models` responde `{"data": [...]}`. Cada entrada lleva las claves `id, provider_id, model, name, max_input_tokens, max_output_tokens, tools, vision`.

**Reglas que fija el spec:**
- `id` es `f"{provider.id}/{model}"`.
- `name` es `f"{provider.id} · {model}"`.
- La entrada Auto tiene `provider_id=None`, 128000/16000, `tools=True`, `vision=True`.
- **Límites:**
  - Si solo se conoce la ventana total, `max_output` es el valor conocido o `min(16000, ventana // 4)`, y `max_input = ventana - max_output`.
  - Si el upstream no da nada y `model == provider.active_model`, se usan `model_info` (`context_window`, `max_output_tokens`, `supports_tools`, `supports_vision`).
  - Si no hay nada de eso, los valores por defecto son 128000/16000, `tools=True`, `vision=False`.
- **Provider usable:** base local o LAN que responde (`providers_service._is_local_base` + `_is_reachable`), o algún slot de `credentials.credential_slots` con `credentials.api_key_for` no vacío.
- **Filtro de OpenRouter:** si `provider.id == "openrouter"`, solo se dejan los ids que terminan en `:free` más el `active_model`.
- **Si `fetch_model_items` falla** o el provider no tiene `models_endpoint`: queda solo `active_model`, si no está vacío. El `active_model` va siempre aunque el listado no lo traiga.
- **Orden:** Auto primero; después los providers en el orden del registro; dentro de cada provider, `active_model` primero y el resto alfabético.
- **Caché:** por `provider.id`, durante `CACHE_TTL_S`.

- [ ] **Paso 1: Escribir los tests que fallan** en `backend/tests/test_copilot_catalog.py`. La fixture monkeypatchea `providers_service.load_registry`, `_is_reachable` y `fetch_model_items`, y llama a `copilot_catalog.clear_cache()`.
  - `test_model_limits_copilot_shape`: con `{"id":"gpt-5.4","capabilities":{"limits":{"max_prompt_tokens":128000,"max_output_tokens":64000},"supports":{"tool_calls":True,"vision":True}}}` devuelve `(128000, 64000, True, True)`.
  - `test_model_limits_openrouter_shape`: con `{"id":"x:free","context_length":262144,"top_provider":{"max_completion_tokens":32768},"architecture":{"input_modalities":["text","image"]},"supported_parameters":["tools"]}` devuelve `(229376, 32768, True, True)`.
  - `test_model_limits_window_only`: con `{"id":"m","context_length":32768}` devuelve `(24576, 8192, None, None)`.
  - `test_model_limits_unknown`: con `{}` devuelve `(0, 0, None, None)`.
  - `test_entry_falls_back_to_model_info_for_active_model`: provider con `active_model="qwen"` y `model_info={"context_window": 65536, "max_output_tokens": 8192, "supports_tools": False}`. `catalog_entry(p, "qwen", None)` da `max_input_tokens == 57344`, `max_output_tokens == 8192` y `tools is False`.
  - `test_catalog_starts_with_auto`: `build_catalog()[0]["id"] == "bipolar-auto"`.
  - `test_cloud_provider_without_key_is_excluded`: un provider cloud sin variable de entorno no aparece.
  - `test_local_provider_without_key_is_included_when_reachable`.
  - `test_openrouter_keeps_only_free_and_active`: items `a:free`, `b`, y el activo `c` dan los modelos `["c", "a:free"]`.
  - `test_failing_models_endpoint_keeps_active_model`: `fetch_model_items` lanza `httpx.ConnectTimeout`. Otro provider sigue apareciendo, y el que falló aporta solo su `active_model`.
  - `test_failing_provider_without_active_model_has_no_entries`.
  - `test_cache_avoids_second_fetch`: dos `build_catalog()` seguidos hacen 1 llamada por provider.
  - `test_endpoint_requires_key` (TestClient sin key → 401).
  - `test_endpoint_returns_data_list` (con key → 200 y `json()["data"][0]["id"] == "bipolar-auto"`).
- [ ] **Paso 2:** Correr `cd backend && pytest tests/test_copilot_catalog.py -q`. Se espera FAIL (módulo inexistente).
- [ ] **Paso 3:** Implementar las interfaces de arriba.
- [ ] **Paso 4:** Correr `cd backend && pytest tests/test_copilot_catalog.py -q`, y también los tests existentes de `list_provider_models` (`pytest -q -k "provider"`). Se espera PASS.
- [ ] **Paso 5: Commit** — `Dominio: catálogo de modelos de bipolar para Copilot (GET /v1/bipolar/models)`

### Tarea 2: Destino fijado en el router

**Archivos:**
- Modificar: `backend/app/services/smart_router.py`
- Test: `backend/tests/test_pinned_route.py` (usa la fixture `env` de `tests/test_route_plan.py` como modelo)

**Interfaces:**
- Consume: `build_plan`, `credentials.credential_slots`, `credentials.api_key_for`, `budget_service.check_budget(registry.smart.budgets, provider_key(id))`, `classify_request` y `decisions_log.insert`.
- Produce:
  - `class PinnedRouteError(Exception)`, con atributos `status: int` y `message: str`;
  - `async decide_pinned(body: dict, provider_id: str, model: str, prompt_tokens: int, *, surface: str = "messages") -> RouteDecision`.
- Contenido de la decisión que devuelve:
  - `source="pinned"`, `mode="pinned"`, `reasons=["pinned"]`;
  - `tier`, `score` e `intent` salen de `classify_request`, solo para el log;
  - `chosen_provider=provider`, `chosen_model=model or None`, `is_active` según `registry.active_provider_id`;
  - `plan = build_plan((provider, model or None, is_active), [], registry.smart.max_failover_attempts)`.
- Registra la fila con `_spawn(decisions_log.insert(...))`. Nunca toca `_sticky`. `note_success` ya sale temprano porque `mode != "active"`.
- **Errores:**
  - `PinnedRouteError(400, f"El provider '{provider_id}' no existe en bipolar")`;
  - `PinnedRouteError(400, f"El provider '{provider_id}' no tiene credencial configurada")`, solo si la base no es local;
  - `PinnedRouteError(429, f"Presupuesto agotado para '{provider_id}': {reason}")`.

- [ ] **Paso 1: Escribir los tests que fallan**
  - `test_unknown_provider_raises_400`.
  - `test_cloud_provider_without_key_raises_400`.
  - `test_local_provider_without_key_is_allowed`.
  - `test_plan_uses_only_pinned_provider_slots`: p1 con dos llaves y `fallback_provider_ids=["p2"]` dan las etiquetas `["p1#0", "p1#1"]`.
  - `test_cooling_pinned_provider_still_tried`: p1 en cooling da `["p1#0"]`.
  - `test_budget_exceeded_raises_429`: monkeypatch de `budget_service.check_budget` para que devuelva `"daily_usd"`.
  - `test_routing_rule_is_ignored`: una regla con `pattern="m1"` hacia p2 no cambia el plan de p1.
  - `test_pinned_never_touches_sticky`: después de `decide_pinned` y de `note_success(decision, step_de_otro_provider, body)`, `smart_router._sticky` queda vacío.
- [ ] **Paso 2:** Correr `cd backend && pytest tests/test_pinned_route.py -q`. Se espera FAIL.
- [ ] **Paso 3:** Implementar `decide_pinned` y `PinnedRouteError`.
- [ ] **Paso 4:** Correr `cd backend && pytest tests/test_pinned_route.py tests/test_route_plan.py tests/test_smart_router.py -q`. Se espera PASS.
- [ ] **Paso 5: Commit** — `Dominio: destino fijado por provider en el router (decide_pinned)`

### Tarea 3: `/v1/messages` con destino fijado y Auto

**Archivos:**
- Modificar: `backend/app/api/messages.py` (`messages_passthrough` y helpers nuevos)
- Modificar: `backend/app/main.py` (`allow_headers` de CORS suma `"X-Bipolar-Provider"`)
- Test: `backend/tests/test_messages_copilot.py` (copia la fixture `harness`, `FakeResp` y `FakeClient` de `tests/test_messages_failover.py`)

**Interfaces:**
- Consume: `smart_router.decide_pinned` y `PinnedRouteError` (Tarea 2); `copilot_catalog.AUTO_MODEL_ID` y `DEFAULT_MAX_INPUT` (Tarea 1).
- Produce:
  - `_copilot_context_window(provider: Provider | None, model: str | None) -> int`: devuelve `model_info["context_window"]` si `model` es el `active_model` (o vacío) y ese valor es mayor que 0; si no, `DEFAULT_MAX_INPUT`.
  - `_steps_with_model(plan: list[PlanStep]) -> list[PlanStep]`: descarta los pasos sin `step.model` ni `provider.active_model`.
  - `_attempt_model(step: PlanStep, model: str) -> str`: devuelve `model`, salvo que sea `AUTO_MODEL_ID`; en ese caso devuelve `step.model or step.provider.active_model`.
- **Flujo nuevo** (solo cuando hay encabezado o el modelo es `bipolar-auto`):
  1. Con encabezado, se decide con `decide_pinned`. Si lanza `PinnedRouteError`, se responde `JSONResponse(e.status, upstream.anthropic_error_body(e.status, e.message))`.
  2. La ventana de contexto se recalcula después de decidir, con `_copilot_context_window`.
  3. Si el modelo es Auto, el plan pasa por `_steps_with_model`. Si queda vacío, se responde 400 «Bipolar Auto no encontró un modelo: configura active_model en el provider elegido».
  4. `open_attempt` le pasa `_attempt_model(step, model)` a `_open_attempt`.
- El camino viejo queda idéntico.

- [ ] **Paso 1: Escribir los tests que fallan.** Hay tres providers: p1 y p2 como en el harness de failover, y p3 sin credencial con `api_base="https://p3.example.com/v1"`.
  - `test_pinned_goes_only_to_pinned_provider`: header `X-Bipolar-Provider: p2` y `model "m2"`. Solo se llama a `P2`, `json["model"] == "m2"` y `x-bipolar-target == "p2#0"`.
  - `test_pinned_failure_does_not_fall_to_other_provider`: `P1` con 503 deja status 502, y no hay ninguna llamada a `P2`.
  - `test_pinned_unknown_provider_returns_400_anthropic_error`: el cuerpo trae `{"type": "error", ...}`.
  - `test_pinned_without_credential_returns_400` (p3).
  - `test_auto_sends_active_model_never_literal`: `model "bipolar-auto"`, y el upstream recibe `json["model"] == "m1"`.
  - `test_auto_without_any_model_returns_400`: con `active_model=""` en p1 y en p2.
  - `test_pinned_large_prompt_not_truncated`: un mensaje de `"palabra " * 20000` (más de 8192 tokens). El upstream recibe el mismo número de mensajes y el texto completo.
  - `test_auto_large_prompt_not_truncated`: lo mismo, con Auto.
  - `test_no_header_claude_model_path_unchanged`: `BODY` original. Primera llamada a `P1` y `x-bipolar-target == "p1#0"`.
- [ ] **Paso 2:** Correr `cd backend && pytest tests/test_messages_copilot.py -q`. Se espera FAIL.
- [ ] **Paso 3:** Implementar el flujo nuevo y agregar el header a CORS.
- [ ] **Paso 4:** Correr `cd backend && pytest -q`. La suite completa debe quedar en PASS.
- [ ] **Paso 5: Commit** — `Aplicación: /v1/messages acepta X-Bipolar-Provider y el modelo bipolar-auto`

### Tarea 4: Extensión — andamiaje, configuración y catálogo

**Archivos:**
- Crear: `integrations/vscode-copilot/package.json`, `tsconfig.json`, `.vscodeignore`, `.gitignore` (`out/`, `node_modules/`, `*.vsix`) y `LICENSE` (copia del MIT del repo)
- Crear: `integrations/vscode-copilot/src/catalog.ts`
- Test: `integrations/vscode-copilot/src/test/catalog.test.ts`

**`package.json`:**
- `name: "bipolar-copilot"`, `displayName: "Bipolar para Copilot"`, `publisher: "santiquiroz"`, `version: "0.1.0"`, `license: "MIT"`;
- `engines.vscode: "^1.127.0"`, `main: "./out/extension.js"`;
- `activationEvents: ["onLanguageModelChatProvider:bipolar"]`;
- `contributes.languageModelChatProviders`: `[{ vendor: "bipolar", displayName: "Bipolar", configuration: { type: "object", properties: { url: { type: "string", title: "URL de bipolar", default: "http://localhost:8000" }, apiKey: { type: "string", title: "API key de bipolar", secret: true } }, required: ["url", "apiKey"] } }]`;
- `contributes.commands`: `[{ command: "bipolar.refreshModels", title: "Bipolar: Refresh models" }]`;
- `scripts`: `compile` = `tsc -p .`; `test` = `tsc -p . && node --test out/test/`; `package` = `npx @vscode/vsce package --allow-missing-repository`.

**Interfaces** (`catalog.ts`, sin importar `vscode`):
- Produce:
  - `interface CatalogEntry { id: string; provider_id: string | null; model: string; name: string; max_input_tokens: number; max_output_tokens: number; tools: boolean; vision: boolean }`
  - `interface BipolarConfig { url: string; apiKey: string }`
  - `interface BipolarModelInfo { id: string; name: string; family: string; version: string; maxInputTokens: number; maxOutputTokens: number; capabilities: { toolCalling: boolean; imageInput: boolean }; providerId: string | null; model: string; url: string; apiKey: string }`. Es estructuralmente compatible con `vscode.LanguageModelChatInformation`; los campos extra viajan con el objeto.
  - `normalizeConfig(raw: unknown): BipolarConfig | undefined`: si falta la `url` usa `http://localhost:8000`; devuelve `undefined` si `apiKey` está vacío.
  - `catalogUrl(baseUrl: string): string` y `messagesUrl(baseUrl: string): string`: quitan la `/` final y agregan `/v1/bipolar/models` y `/v1/messages`.
  - `parseCatalog(json: unknown): CatalogEntry[]`: lanza `Error("Respuesta de catálogo inválida")` si no hay un arreglo `data`, y descarta las entradas sin `id` o sin `model`.
  - `toModelInfo(entry: CatalogEntry, config: BipolarConfig): BipolarModelInfo`: `family` es `entry.provider_id ?? "bipolar"` y `version` es `"1"`.

- [ ] **Paso 1: Escribir los tests que fallan**
  - `normalizeConfig({apiKey:"k"})` da `{url:"http://localhost:8000", apiKey:"k"}`.
  - `normalizeConfig({url:"x"})` da `undefined`.
  - `catalogUrl("http://h:8000/")` da `"http://h:8000/v1/bipolar/models"`.
  - `parseCatalog({data:[ok, {id:"x"}]})` devuelve solo `ok`.
  - `parseCatalog({})` lanza el error.
  - `toModelInfo` de la entrada Auto da `family "bipolar"`, `providerId null` y `capabilities.toolCalling true`.
- [ ] **Paso 2:** Correr `cd integrations/vscode-copilot && npm install && npm test`. Se espera FAIL.
- [ ] **Paso 3:** Implementar `catalog.ts` y la configuración del paquete.
- [ ] **Paso 4:** Correr `npm test`. Se espera PASS.
- [ ] **Paso 5: Commit** — `Infraestructura: extensión de VS Code bipolar-copilot (andamiaje y catálogo)`

### Tarea 5: Extensión — conversión a Anthropic y lectura del SSE

**Archivos:**
- Crear: `integrations/vscode-copilot/src/convert.ts` y `src/sse.ts`
- Test: `src/test/convert.test.ts` y `src/test/sse.test.ts`

**Interfaces** (sin importar `vscode`; las partes se reconocen por su forma):
- `convert.ts`:
  - **Formas de las partes:** texto `{value: string}`; tool call `{callId, name, input}`; tool result `{callId, content: unknown[]}`; dato `{mimeType: string, data: Uint8Array}`.
  - **Roles:** `ROLE_USER = 1` y `ROLE_ASSISTANT = 2`, los valores de `vscode.LanguageModelChatMessageRole`.
  - `toAnthropicMessages(messages: readonly {role: number; content: readonly unknown[]}[]): AnthropicMessage[]`:
    - junta mensajes consecutivos del mismo rol y descarta los vacíos;
    - un tool result se convierte en `{type:"tool_result", tool_use_id, content}`, donde `content` es el texto de sus partes unido con `"\n"`;
    - una imagen (`mimeType` que empieza con `image/`) se convierte en `{type:"image", source:{type:"base64", media_type, data}}`;
    - los otros `mimeType` se ignoran, por ejemplo las pistas de caché de Copilot.
  - `toAnthropicTools(tools?: readonly {name: string; description: string; inputSchema?: object}[]): AnthropicTool[] | undefined`: si falta `inputSchema` usa `{type:"object", properties:{}}`.
  - `buildRequestBody(model: {model: string; maxOutputTokens: number}, messages, tools, toolMode?: number): object`: produce `{model, max_tokens, messages, stream: true, tools?, tool_choice?}`. Con `toolMode === 2` (Required) agrega `tool_choice: {type:"any"}`.
- `sse.ts`:
  - `type StreamEvent = {kind:"text"; text: string} | {kind:"tool_call"; callId: string; name: string; input: object} | {kind:"error"; message: string}`.
  - `class AnthropicSseParser { push(chunk: string): StreamEvent[]; end(): StreamEvent[] }`:
    - acumula entre chunks, también cuando el corte cae a mitad de una línea;
    - `content_block_start` con `tool_use` abre un buffer por `index`;
    - `text_delta` emite texto;
    - `input_json_delta` acumula en el buffer;
    - `content_block_stop` emite el `tool_call` con `JSON.parse(buffer || "{}")`;
    - un evento `error` emite `error`;
    - ignora `thinking`, `ping` y `message_*`.

- [ ] **Paso 1: Escribir los tests que fallan**
  - **Convert:**
    - texto de usuario;
    - un tool call del asistente seguido del tool result del usuario conserva el par `tool_use.id == tool_result.tool_use_id`;
    - dos mensajes de usuario seguidos se juntan en uno;
    - una imagen PNG sale en base64;
    - una parte `mimeType "cache_control"` se ignora;
    - con `toolMode 2` aparece `tool_choice`;
    - una herramienta sin esquema recibe el esquema por defecto.
  - **SSE:**
    - texto partido a mitad de línea entre dos `push`;
    - tool call con `partial_json` en 3 deltas que da `input` completo, emitido una sola vez;
    - dos tool calls con `index` 1 y 2 intercaladas;
    - evento `error`;
    - `input_json_delta` ausente da `input {}`.
- [ ] **Paso 2:** Correr `npm test`. Se espera FAIL.
- [ ] **Paso 3:** Implementar los dos módulos.
- [ ] **Paso 4:** Correr `npm test`. Se espera PASS.
- [ ] **Paso 5: Commit** — `Infraestructura: conversión de mensajes de VS Code a Anthropic y parser SSE`

### Tarea 6: Extensión — proveedor conectado a VS Code y README de la integración

**Archivos:**
- Crear: `integrations/vscode-copilot/src/provider.ts`, `src/extension.ts` y `README.md`

**Interfaces:**
- Consume: todo lo de las Tareas 4 y 5.
- Produce: `class BipolarChatProvider implements vscode.LanguageModelChatProvider<BipolarModelInfo>`, con `onDidChangeLanguageModelChatInformation` (un `EventEmitter`) y `refresh(): void`.
- `provideLanguageModelChatInformation({silent, configuration}, token)`:
  1. `normalizeConfig`; si no hay configuración, devuelve `[]`.
  2. `GET catalogUrl` con `x-api-key`, un timeout de 10 s y un `AbortController` atado a `token`.
  3. Si la respuesta no es 200, escribe en el canal de salida «Bipolar» y, si `silent`, devuelve `[]`; si no, lanza un `Error` legible (401 → «API key de bipolar inválida»).
  4. Devuelve `parseCatalog(...).map(e => toModelInfo(e, config))`.
- `provideLanguageModelChatResponse(model, messages, options, progress, token)`:
  - hace `POST messagesUrl(model.url)` con `x-api-key`, `anthropic-version: 2023-06-01` y `content-type`, más `X-Bipolar-Provider: model.providerId` cuando no es `null`;
  - el cuerpo sale de `buildRequestBody(model, toAnthropicMessages(messages), toAnthropicTools(options.tools), options.toolMode)`;
  - si la respuesta no es 2xx, lanza `Error(error.message del cuerpo Anthropic || "bipolar respondió <status>")`;
  - lee el stream y lo pasa por `AnthropicSseParser`: `text` se reporta como `new vscode.LanguageModelTextPart`, `tool_call` como `new vscode.LanguageModelToolCallPart(callId, name, input)` y `error` se lanza como `Error`;
  - la cancelación aborta el `fetch`.
- `provideTokenCount(model, text, token)`: `Math.ceil(caracteres / 4)` sobre el texto, o la suma de las partes de texto del mensaje.
- `extension.ts` (`activate`): crea el canal de salida «Bipolar», registra `vscode.lm.registerLanguageModelChatProvider("bipolar", provider)` y el comando `bipolar.refreshModels` → `provider.refresh()`.
- **`README.md`** de la integración, para alguien que nunca vio bipolar:
  - requisitos (bipolar corriendo, VS Code ≥ 1.127 con Copilot Chat, Node ≥ 22);
  - construir: `npm install`, `npm test`, `npm run package`;
  - instalar el `.vsix` («Extensions → … → Install from VSIX…»), o el CLI de VS Code por su ruta completa, porque en algunos PCs `code` abre Cursor;
  - agregar el proveedor: «Manage Models → Add Models → Bipolar», con la URL y la `ui_api_key` (dónde se ve: Settings de bipolar o `.env` del directorio de configuración);
  - usar Auto o un modelo fijado;
  - comprobar el destino: encabezado `X-Bipolar-Route` y página de decisiones de bipolar;
  - problemas frecuentes: 401, catálogo vacío (key, bipolar caído, ningún provider usable), modelo sin herramientas que no aparece en modo agente, provider fijado caído.

- [ ] **Paso 1:** Implementar `provider.ts` y `extension.ts`.
- [ ] **Paso 2:** Correr `cd integrations/vscode-copilot && npm test && npm run package`. Se espera PASS y que exista `bipolar-copilot-0.1.0.vsix`.
- [ ] **Paso 3:** Escribir `README.md`.
- [ ] **Paso 4: Commit** — `Infraestructura: proveedor Bipolar para Copilot Chat y guía de instalación`

### Tarea 7: Documentación del repo, versión y prueba real

**Archivos:**
- Modificar: `README.md`. Reescribir «Caso de uso 4»: primero el proveedor Bipolar en Copilot, con un enlace a `integrations/vscode-copilot/README.md`; después el texto OpenAI-compatible, para Cursor, Cline y Continue. Quitar la instrucción de «Copilot → OpenAI compatible», que está deprecada.
- Modificar: `CLAUDE.md`. Agregar un párrafo de arquitectura «Proveedor de Copilot (2.21)» con `copilot_catalog`, `GET /v1/bipolar/models`, `X-Bipolar-Provider` → `decide_pinned` (sin sticky, sin failover y con presupuesto), `bipolar-auto` (ventana 128000 por defecto, nunca se reenvía) y `integrations/vscode-copilot/`.
- Modificar: la versión 2.21.0 en `backend/app/main.py` (`APP_VERSION`), `frontend/package.json` y `frontend/package-lock.json`.

- [ ] **Paso 1:** Escribir la documentación y subir la versión.
- [ ] **Paso 2:** Correr `cd backend && pytest -q`. Se espera PASS en la suite completa.
- [ ] **Paso 3: Prueba real** (orquestador y Santiago; ver el spec, «Cómo se verifica»):
  1. Reiniciar bipolar con la rama.
  2. Instalar el `.vsix`.
  3. Agregar Bipolar.
  4. Ver el catálogo.
  5. Chatear con un modelo local fijado y con uno cloud fijado.
  6. Usar Auto.
  7. Hacer una tarea en modo agente que lea y edite un archivo.
  8. Revisar las decisiones (`pinned` y las de Auto).
  9. Comprobar en paralelo que una sesión de Claude Code y un `delegate` por MCP siguen funcionando.
- [ ] **Paso 4: Commit** — `Configuración: versión 2.21.0 y documentación del proveedor de Copilot`
