# Bipolar como proveedor de modelos en GitHub Copilot Chat

Fecha: 2026-10-27 · Estado: aprobado por Santiago en conversación (2026-10-27)

## Intención

Que bipolar-code se pueda usar desde GitHub Copilot Chat (VS Code) como un proveedor de modelos más, igual que hoy se agregan Anthropic, OpenAI u Ollama: en «Manage Models» aparece **Bipolar**, se configura con la URL y la key de bipolar, y el selector de modelos se llena solo con lo que bipolar tiene. Desde ahí se elige un modelo concreto o **Bipolar Auto**, y bipolar decide.

Santiago lo plantea como **compatibilidad de bipolar con Copilot**, no como un producto aparte. Por ahora es para uso local: nada se publica en el Marketplace ni se expone fuera del PC.

## Lo que ya sabemos de Copilot (investigado el 2026-10-27)

- Desde 2026 Copilot Chat vive dentro de `microsoft/vscode` (`extensions/copilot`). Sus proveedores BYOK «Ollama» y «OpenAI Compatible» están marcados como *Deprecated*. El reemplazo de Ollama es la extensión oficial `Ollama.ollama` (MIT, `github.com/ollama/ollama-vscode`), instalada en este PC en la 0.0.14.
- Esa extensión es el patrón a seguir: registra un vendor propio con `vscode.lm.registerLanguageModelChatProvider` (API estable, sin propuestas), declara `contributes.languageModelChatProviders` con un esquema de configuración (`url`, `headers`), descubre los modelos y los entrega a Copilot. Copilot la muestra como un proveedor más en «Manage Models».
- El proveedor «Custom Endpoint» de Copilot sirve para pruebas, pero exige escribir la lista de modelos a mano. No cumple «se llena solo».

## Objetivos

1. **Proveedor «Bipolar» en Copilot Chat**, mediante una extensión de VS Code propia con vendor `bipolar`. La configuración pide `url` (por defecto `http://localhost:8000`) y `apiKey` (secreto; acepta la `ui_api_key` de bipolar).
2. **Catálogo automático**: la extensión le pide a bipolar su lista de modelos y Copilot la muestra. Cada entrada es una pareja provider/modelo de bipolar con un nombre legible («llamacpp · qwen3-coder», «copilot · gpt-5.4»), su ventana de contexto, su salida máxima y si admite herramientas e imágenes.
3. **Elegir un modelo fija el destino**: la petición va a ese provider y a ese modelo, sin pasar por reglas de ruteo ni por el tier.
4. **Bipolar Auto**: una entrada más del catálogo. Con ella bipolar decide por cada mensaje exactamente como decide hoy para una petición sin modelo fijado (reglas por escenario, smart router si está activo, salud, cuotas y failover).
5. **Herramientas (modo agente) y streaming** funcionando: sin tool calling, Copilot no sirve en modo agente.
6. **Instrucciones claras y sencillas en el repo** para que otra persona pueda replicar todo sin preguntar (pedido de Santiago):
   - `integrations/vscode-copilot/README.md`: requisitos, cómo construir el `.vsix`, cómo instalarlo (incluido el caso en que `code` apunta a Cursor), cómo agregar Bipolar en «Manage Models», cómo usar Auto o un modelo fijado, cómo comprobar en el log de decisiones a dónde fue cada petición, y problemas frecuentes (401, catálogo vacío, modelo sin herramientas).
   - En el README raíz, reescribir el «Caso de uso 4», que hoy manda a «OpenAI compatible», ya deprecado en Copilot, para que apunte al proveedor Bipolar. El texto queda para Cursor, Cline y Continue.
   - En `CLAUDE.md`, un párrafo de arquitectura con el catálogo, el encabezado `X-Bipolar-Provider`, `bipolar-auto` y la extensión.

## No objetivos

- Pasar por Ollama o emular su API.
- Publicar la extensión en el Marketplace o exponer bipolar fuera de la máquina.
- Delegar tareas a agentes CLI (Muse, Codex…) desde el chat de Copilot. «Auto» significa elegir el modelo que responde, no lanzar un job del broker.
- Bloques de razonamiento (thinking) visibles en Copilot. Quedan para después.
- Autocompletado inline (ghost text). Esta API solo cubre el chat.

## Anti-objetivos: sería un fracaso aunque funcione

- **Cualquier cambio en la dinámica actual de delegación y ruteo.** Claude Code contra `/v1/messages`, el broker y el MCP (`delegate`, `job_status`…), los agentes CLI, sus tiers y cuentas, el provider activo, las reglas de ruteo y lo que bipolar escribe en `~/.claude/settings.json` y en el registro de Windows deben comportarse igual que antes. Una petición sin la marca nueva (ver «Cómo se fija el destino») tiene que seguir el mismo camino de siempre, byte a byte.
- Cambiar la respuesta actual de `GET /v1/models`. Hoy la consumen otros clientes.
- Que fijar un modelo modifique la configuración de bipolar (provider activo, reglas, `active_model`). Fijar vale solo para esa petición.
- Que la key de bipolar quede en texto plano en `settings.json` de VS Code. Debe ir al almacenamiento de secretos de la configuración de proveedores.

## Decisiones tomadas

| Tema | Decisión | De quién |
|---|---|---|
| Forma de integrarse | Extensión propia con vendor `bipolar`, siguiendo el patrón de `Ollama.ollama` | Santiago (dijo «otro proveedor de Copilot», no por Ollama) |
| Elegir modelo + Auto | Ambos | Santiago |
| Qué es «Auto» | Ruteo por mensaje de bipolar, no delegación a agentes CLI | Santiago |
| No dañar la delegación actual | Restricción dura | Santiago |
| Alcance | Uso local, sin publicar | Santiago |
| Formato de cable extensión ↔ bipolar | Anthropic Messages contra `POST /v1/messages`. Es el camino que usa Claude Code: llega a todos los providers (traduce a chat completions donde hace falta, pasa directo a Anthropic y a los nativos) y ya trae compresión y reintento por contexto. `/v1/chat/completions` rechaza los providers Anthropic que no son el activo (`oai_surface_anthropic_not_active`) | Mía |
| Cómo se fija el destino | Encabezado `X-Bipolar-Provider: <provider_id>`, con `model` = el modelo real. Sin ese encabezado no cambia nada. Se descartó codificar el provider en el nombre del modelo (`anthropic/…`) porque los ids de OpenRouter ya usan `/` y chocarían con nombres reales | Mía |
| Si el destino fijado falla | Rotar solo entre las llaves (slots) de ese provider. Sin failover a otro provider: el error llega a Copilot con un mensaje claro. Si eligió un modelo concreto, no se le cambia sin avisar | Mía (Santiago puede cambiarlo) |
| Auto en el cable | Sin encabezado y con `model` = `bipolar-auto`. Bipolar nunca reenvía el literal `bipolar-auto` a un upstream: si ninguna regla da modelo, usa el `active_model` del provider elegido | Mía |
| Dónde vive la extensión | En este repo, `integrations/vscode-copilot/` (TypeScript, sin dependencias de runtime si `fetch` de Node alcanza) | Mía |
| Instalación | `.vsix` generado con `@vscode/vsce` e instalado a mano. **Ojo:** en este PC el comando `code` del PATH abre Cursor (`code --version` → 3.24.12), así que hay que usar el CLI de VS Code por su ruta completa o «Install from VSIX…» | Mía |
| Conteo de tokens | Estimación local (caracteres / 4), como hacen otros proveedores BYOK | Mía |

## Piezas

### En bipolar (backend)

1. **Catálogo**: un endpoint nuevo bajo `/v1` (protegido por la misma key que `/v1/*`) que devuelve:
   - una entrada `bipolar-auto` («Bipolar Auto»);
   - una entrada por cada modelo de cada provider usable. Usable significa que tiene credencial configurada o, si es local o LAN, que está levantado. Los modelos salen de su `models_endpoint`, con caché de algunos minutos para no golpear upstreams en cada apertura del selector, y `active_model` va siempre aunque el listado falle.
   - En cada entrada: `id` estable, `provider_id`, `model`, `name`, `max_input_tokens`, `max_output_tokens`, `tools`, `vision`. Si el upstream no da los límites, se usan valores por defecto conservadores y lo que bipolar ya aprendió en `Provider.model_info`.
   - **Decide el builder:** cómo extraer límites y capacidades de cada formato de listado (OpenRouter, Copilot, llama-server, LM Studio, Ollama), los valores por defecto, la ruta exacta del endpoint y si conviene filtrar listados enormes. Recomendación: en OpenRouter mostrar solo `:free` y el `active_model`, para no invitar a gastar.
2. **Destino fijado en `/v1/messages`**: con `X-Bipolar-Provider` presente y válido, el plan de ruta es ese provider con ese modelo y sus slots de credencial, sin reglas, sin tier y sin failover a otros providers. Si el provider no existe, falta la credencial o no está disponible, se responde con un error claro (4xx o el status real del upstream). La decisión queda en el log de decisiones como hoy, con la razón «pinned».
3. **Auto**: `model = bipolar-auto` sin encabezado sigue el camino actual de `smart_router.decide` y `pick_provider`, con la única regla nueva de no reenviar ese nombre literal.
4. El encabezado nuevo solo tiene efecto en `POST /v1/messages`. Se agrega a `allow_headers` de CORS en `main.py`, junto a `X-Bipolar-Tier`.

### Contratos precisos (cerrados tras la revisión del builder)

**Catálogo:** `GET /v1/bipolar/models`. Usa la misma autenticación que el resto de `/v1/*` (`ui_api_key` o `proxy_api_key`, según `middleware/auth.py`). Responde:

```json
{"data": [
  {"id": "bipolar-auto", "provider_id": null, "model": "bipolar-auto", "name": "Bipolar Auto",
   "max_input_tokens": 128000, "max_output_tokens": 16000, "tools": true, "vision": true},
  {"id": "llamacpp/qwen3-coder", "provider_id": "llamacpp", "model": "qwen3-coder", "name": "llamacpp · qwen3-coder",
   "max_input_tokens": 120000, "max_output_tokens": 8000, "tools": true, "vision": false}
]}
```

- `id` es opaco para bipolar: la extensión lo usa solo como id de modelo en VS Code. A bipolar le manda `provider_id` (en el encabezado) y `model` (en el cuerpo) por separado, así que un `/` dentro de `model` (OpenRouter) no molesta.
- **Provider usable:** su base es local o LAN y responde, o tiene al menos un slot de credencial con valor. Esto incluye el token con refresco de `copilot`.
- Si el listado falla y `active_model` está vacío, el provider no aparece.
- **Se incluye el provider `copilot` de bipolar.** Auto ya puede rutear ahí, y Copilot deja ocultar modelos desde «Manage Models».

**Destino fijado** (`X-Bipolar-Provider` presente en `/v1/messages`):

- Se salta reglas, tier, ranking y stickiness. **No lee ni escribe `_sticky`**, para que una petición en Auto de la misma conversación no herede el provider fijado.
- Sí se registra en el log de decisiones con la fuente `pinned`. También reporta salud al terminar, porque la salud describe al provider y no a la elección.
- Se intenta aunque el provider esté en cooling: lo eligió el usuario a propósito.
- Los slots se recorren en el orden de `build_plan`. Sin slots sanos se usa el slot 0, como hoy.
- **El presupuesto (`budget_service`) sí se respeta:** fijar un modelo no permite gastar por encima del tope.
- No se filtra por capacidad. El catálogo ya declara `tools` y `vision`, y Copilot no ofrece en modo agente los modelos sin herramientas.
- **Errores:**
  - provider inexistente: 400;
  - provider sin credencial (y no local): 400;
  - presupuesto agotado: 429.
  - Los tres llevan cuerpo de error Anthropic (`{"type":"error","error":{"type":"invalid_request_error"|"rate_limit_error","message":"…"}}`) con un mensaje legible.
  - Si falla el upstream, se usa el camino de agotamiento que ya existe (`upstream.exhausted_status` y los encabezados `X-Bipolar-Target` y `X-Bipolar-Attempts`).

**Auto** (`model = "bipolar-auto"`, sin encabezado):

- Hasta la decisión el nombre viaja tal cual. Las reglas por subcadena no coinciden con él, salvo la regla comodín de patrón vacío, que es lo que se busca. El clasificador simplemente no ve pista de modelo.
- La compresión previa a la decisión usa la ventana por defecto de `token_service` para nombres desconocidos.
- En el punto donde hoy se calcula el modelo del upstream (`step.model or provider.active_model or model` en `_native_request` y `_oai_request`), `bipolar-auto` nunca se usa como último recurso. Si no hay `step.model` ni `active_model`, el paso falla como `fatal` con un 400 claro.

### Extensión `integrations/vscode-copilot/`

- `package.json` con `contributes.languageModelChatProviders: [{ vendor: "bipolar", displayName: "Bipolar", configuration: { url, apiKey(secret) } }]` y el `engines.vscode` mínimo que exija la API.
- `provideLanguageModelChatInformation`: pide el catálogo y lo convierte en modelos de Copilot. Si bipolar no responde: lista vacía y un mensaje en un canal de salida «Bipolar». En modo silencioso no muestra diálogos.
- `provideLanguageModelChatResponse`: convierte los mensajes de VS Code (texto, llamadas a herramientas, resultados de herramientas, imágenes) a Anthropic Messages. Envía `stream: true` con `x-api-key` o `Authorization: Bearer`, y `X-Bipolar-Provider` cuando el modelo no es Auto. Va reportando texto y tool calls a medida que llega el SSE. La cancelación aborta el `fetch`.
- `provideTokenCount`: estimación local.
- Comando «Bipolar: Refresh models».
- **Decide el builder:** la estructura interna, el framework de pruebas de la conversión y cómo cachear el catálogo del lado de la extensión.

## Cómo se verifica

- **Backend (pytest)**:
  - el catálogo incluye Auto y los modelos de providers usables, y no los inutilizables;
  - con `X-Bipolar-Provider` el plan tiene un solo provider y no aplica reglas;
  - sin encabezado, el plan es idéntico al de antes del cambio para las mismas peticiones (prueba de no regresión del camino de Claude Code);
  - una petición fijada no deja rastro en `_sticky`, y la siguiente en Auto de la misma conversación decide como si la fijada no hubiera ocurrido;
  - el presupuesto agotado devuelve 429 también con el destino fijado;
  - `bipolar-auto` nunca llega como nombre a un upstream;
  - la suite completa sigue verde.
- **Extensión**: pruebas unitarias de la conversión de mensajes (texto, tool call con su resultado, imagen) y del parseo del SSE.
- **Prueba real en VS Code**:
  1. Agregar Bipolar en «Manage Models».
  2. Ver el catálogo en el selector.
  3. Chatear con un modelo fijado (uno local y uno cloud).
  4. Usar Bipolar Auto.
  5. Hacer una tarea en modo agente que use herramientas (leer y editar un archivo).
  6. Comprobar en el log de decisiones de bipolar que cada petición fue a donde debía.
  7. Comprobar que una sesión de Claude Code y un `delegate` por MCP siguen funcionando igual mientras tanto.

## Queda para el builder

Todo lo marcado «Decide el builder» y los detalles que no cambian lo que se construye: nombres internos, mensajes de error, orden de los modelos en el catálogo, iconos y textos de la extensión.
