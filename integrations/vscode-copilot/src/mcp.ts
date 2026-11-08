import * as vscode from "vscode";
import { BipolarConfig, DEFAULT_URL, mcpUrl } from "./catalog";

const URL_KEY = "bipolar.mcp.url";
const API_KEY = "bipolar.mcp.apiKey";

export class BipolarMcpProvider
  implements vscode.McpServerDefinitionProvider<vscode.McpHttpServerDefinition>, vscode.Disposable
{
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeMcpServerDefinitions = this.changed.event;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  dispose(): void {
    this.changed.dispose();
  }

  async setConfig(cfg: BipolarConfig): Promise<void> {
    const sameUrl = this.context.globalState.get<string>(URL_KEY) === cfg.url;
    const sameKey = (await this.context.secrets.get(API_KEY)) === cfg.apiKey;
    if (sameUrl && sameKey) {
      return;
    }
    await this.context.globalState.update(URL_KEY, cfg.url);
    await this.context.secrets.store(API_KEY, cfg.apiKey);
    this.output.appendLine(`Servidor MCP de bipolar configurado en ${mcpUrl(cfg.url)}`);
    this.changed.fire();
  }

  provideMcpServerDefinitions(): vscode.McpHttpServerDefinition[] {
    // sin proveedor agregado todavía se ofrece el bipolar local; la clave se pide al primer uso
    const url = this.context.globalState.get<string>(URL_KEY) || DEFAULT_URL;
    return [new vscode.McpHttpServerDefinition("bipolar", vscode.Uri.parse(mcpUrl(url)), {}, "1")];
  }

  async resolveMcpServerDefinition(
    server: vscode.McpHttpServerDefinition,
  ): Promise<vscode.McpHttpServerDefinition | undefined> {
    const apiKey = await this.readOrAskKey();
    if (apiKey === undefined) {
      return undefined;
    }
    server.headers = { "x-api-key": apiKey };
    return server;
  }

  private async readOrAskKey(): Promise<string | undefined> {
    const stored = await this.context.secrets.get(API_KEY);
    if (stored !== undefined && stored !== "") {
      return stored;
    }
    const typed = await vscode.window.showInputBox({
      prompt: "API key de bipolar (ui_api_key)",
      password: true,
      ignoreFocusOut: true,
    });
    if (typed === undefined || typed === "") {
      return undefined;
    }
    await this.context.secrets.store(API_KEY, typed);
    return typed;
  }
}
