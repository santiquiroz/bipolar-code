# Bipolar para Copilot

Extensión de VS Code que agrega **bipolar-code** como proveedor de modelos en **GitHub Copilot Chat**.

bipolar-code es un proxy local que reparte tus peticiones entre varios providers de modelos (locales y en la nube) con reglas, ruteo inteligente y failover. Con esta extensión, el selector de modelos de Copilot Chat muestra:

- **Bipolar Auto**: bipolar decide a dónde va cada mensaje.
- Una entrada **«&lt;provider&gt; · &lt;modelo&gt;»** por cada provider y modelo usable que tenga configurado bipolar.

## Requisitos

- bipolar-code corriendo (por defecto en `http://localhost:8000`).
- VS Code 1.127 o superior con la extensión **GitHub Copilot Chat**.
- Node.js 22 o superior, solo para construir la extensión.

## Construir

Desde `integrations/vscode-copilot/`:

```bash
npm install
npm test
npm run package
```

El último comando genera `bipolar-copilot-0.1.0.vsix`.

## Instalar

Opción 1, desde VS Code: vista **Extensions** → menú **«…»** → **«Install from VSIX…»** → elige `bipolar-copilot-0.1.0.vsix`.

Opción 2, con el CLI de VS Code. Usa la ruta completa, porque en algunos PCs el comando `code` abre Cursor en vez de VS Code:

```bat
"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd" --install-extension bipolar-copilot-0.1.0.vsix
```

## Agregar el proveedor

1. Abre Copilot Chat y despliega el selector de modelos.
2. Elige **«Manage Models…»** → **«Add Models…»** → **«Bipolar»**.
3. Escribe la URL de bipolar: `http://localhost:8000`.
4. Escribe la API key de bipolar (`ui_api_key`). La encuentras en la página **Settings** de bipolar o en el archivo `.env` de su directorio de configuración: `C:\litellm` en Windows y `~/.litellm` en los demás sistemas.

## Usar

- **Bipolar Auto**: bipolar rutea cada mensaje con sus reglas, el ruteo inteligente y el failover entre providers.
- **«&lt;provider&gt; · &lt;modelo&gt;»**: fuerza ese provider. Un provider forzado **nunca** cae a otro provider: si falla, ves el error.

## Comprobar a dónde fue cada petición

- El encabezado de respuesta `X-Bipolar-Route` indica el provider y el modelo que atendieron la petición.
- La vista de decisiones de ruteo de bipolar muestra cada decisión. Las peticiones con un provider forzado aparecen con origen `pinned`.

## Copilot CLI y las herramientas

Copilot Chat ejecuta las sesiones con el agente de **Copilot CLI**. Antes de enviar un mensaje, comprueba que el prompt del sistema y la definición de todas las herramientas quepan en el contexto del modelo. Solo con algunas familias de modelos (por ejemplo, Claude) carga las herramientas MCP **bajo demanda**. Con cualquier otro modelo las incluye todas de entrada; si no caben, el turno falla con **«No response was returned»** y la petición nunca llega a bipolar.

Para evitarlo, la extensión ajusta sola el setting de usuario `chat.agentHost.copilot.modelCapabilityOverrides` cada vez que carga el catálogo:

- A cada modelo de Bipolar le asigna la familia `claude-sonnet-4.6`, así Copilot CLI carga las herramientas bajo demanda.
- A los modelos con menos de 64K tokens de contexto, además, les deja solo las herramientas integradas (`availableTools: ["builtin:*"]`), porque ni siquiera caben las herramientas diferidas.
- Respeta las entradas que hayas escrito a mano: nunca las sobrescribe. Solo cambia o borra las entradas que creó ella misma.
- Después de la primera carga de modelos, **abre un chat nuevo** para que Copilot tome el ajuste.

Si prefieres gestionarlo tú, desactívalo con `"bipolar.manageCopilotOverrides": false` en tus settings.

## Problemas frecuentes

- **Error 401 / «API key de bipolar inválida»**: la `ui_api_key` es incorrecta. Vuelve a agregar el proveedor con la key correcta.
- **La lista de modelos está vacía**: bipolar no está corriendo, la URL es incorrecta o no hay ningún provider usable. Ejecuta el comando **«Bipolar: Refresh models»** y revisa el canal de salida **«Bipolar»** (vista **Output**).
- **Un modelo no aparece en modo agente**: el modo agente solo muestra modelos con soporte de herramientas.
- **«No response was returned»**: abre un chat nuevo para que Copilot tome el ajuste de `chat.agentHost.copilot.modelCapabilityOverrides` (ver «Copilot CLI y las herramientas»). Si sigue fallando, revisa el canal de salida **«Bipolar»** (vista **Output**).
- **El provider forzado está caído**: Copilot muestra el error en vez de pasar en silencio a otro provider. Usa Bipolar Auto o elige otro provider.

## Cómo funciona

- El catálogo sale de `GET /v1/bipolar/models` (con el encabezado `x-api-key`).
- El chat va por `POST /v1/messages` (formato de Anthropic, en streaming). Si elegiste un provider, la petición lleva el encabezado `X-Bipolar-Provider` con su id.
- Bipolar Auto usa el modelo `bipolar-auto` y no envía `X-Bipolar-Provider`, así que bipolar decide.
