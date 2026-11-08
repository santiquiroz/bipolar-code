# Delegar código a bipolar desde Copilot Chat

## Qué es

Puedes delegar tareas de código a bipolar desde GitHub Copilot Chat igual que desde Claude Code: bipolar expone un servidor MCP en `http://<host>:8000/mcp` con las herramientas `delegate`, `job_status`, `job_output`, `cancel_job` y `list_agents`.

```
Copilot Chat -> MCP bipolar -> broker -> Muse / Codex / Copilot CLI / Claude Code
             -> verify + revisión -> diff en tu repo
```

## Opción A (recomendada): extensión Bipolar para Copilot 0.2.0

1. Construye e instala el `.vsix` (ver `integrations/vscode-copilot/README.md`): `bipolar-copilot-0.2.0.vsix`.
2. Agrega el proveedor: Copilot Chat → «Manage Models…» → «Add Models…» → «Bipolar», con la URL de bipolar y la `ui_api_key`.
3. El servidor MCP **bipolar** aparece solo (apunta a `http://localhost:8000` hasta que agregues el proveedor; la clave se pide la primera vez). Compruébalo en Copilot Chat → herramientas, o con el comando **«MCP: List Servers»**.
4. Ejecuta el comando **«Bipolar: Instalar agente orquestador en este workspace»**. Crea `.github/agents/bipolar-orquestador.agent.md`.
5. En el selector de agentes del chat, elige **Bipolar Orquestador**.

## Opción B: sin la extensión

Crea `.vscode/mcp.json` en el workspace:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "bipolar-key",
      "description": "API key de bipolar (ui_api_key)",
      "password": true
    }
  ],
  "servers": {
    "bipolar": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "x-api-key": "${input:bipolar-key}" }
    }
  }
}
```

Luego copia el agente desde `integrations/vscode-copilot/src/agentTemplate.ts` a `.github/agents/bipolar-orquestador.agent.md`. También puedes ponerlo en `~/.copilot/agents/` para que valga en todos los proyectos.

## Copilot CLI

Crea `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "bipolar": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "x-api-key": "<ui_api_key>" },
      "tools": ["*"]
    }
  }
}
```

Ojo: ese archivo guarda la clave en texto plano.

## Ejemplo de sesión

Pedido: «Agrega validación de email a `registrar_usuario` con sus tests».

El orquestador escribe el contrato:

```
Archivo: app/usuarios.py. Función registrar_usuario(email: str) -> Usuario.
Regla: si email no cumple "algo@dominio.tld", lanzar ValueError("email inválido").
Casos: "a@b.co" -> ok; "a@b" -> ValueError; "" -> ValueError.
Pruebas: tests/test_usuarios.py cubre los tres casos; pytest debe pasar.
Aceptación: no cambia la firma ni otros archivos.
```

Llamada: `delegate` con `task` = el contrato, `workspace` = `C:\repos\mi-app`, `verify` = `pytest tests/test_usuarios.py`, `wait_s` = 0.

Luego `job_status` con `wait_s` 50 en bucle hasta `succeeded`. Al volver, el orquestador revisa `git status` y `git diff` contra el contrato y corre la verificación. Si algo no cumple, devuelve el trabajo al mismo agente citando qué corregir.

## Varias PCs contra un mismo bipolar

- Los agentes corren **en el host** de bipolar. `workspace` debe ser una ruta que exista en ese host y esté en la lista permitida (Agentes → Delegación). Desde otra PC solo se puede delegar sobre repos que estén en el host, o en una carpeta compartida montada en la misma ruta.
- Concurrencia: cada carril tiene `max_concurrency` (Claude Code, 2 por defecto) y el broker encola en vez de mandar el trabajo a un carril pago. Para más paralelo, agrega cuentas (Agentes → Cuentas).
- Seguridad: la `ui_api_key` da control total del broker. Expón bipolar fuera de la LAN solo detrás de un túnel o VPN.

## Problemas comunes

- **401**: la `ui_api_key` es incorrecta. Vuelve a agregar el proveedor o corrige `mcp.json`.
- **«workspace not allowed»**: agrega la ruta a la lista permitida en Agentes → Delegación.
- **El agente no ve las herramientas**: habilita el servidor `bipolar` en el selector de herramientas del chat.
- **Jobs en `quota`**: el carril está agotado; bipolar ya intentó los demás. Espera la renovación de cuota o agrega cuentas.
