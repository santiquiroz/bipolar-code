import * as vscode from "vscode";
import { ORCHESTRATOR_AGENT } from "./agentTemplate";
import { CopilotOverridesSync } from "./copilotSettings";
import { BipolarMcpProvider } from "./mcp";
import { BipolarChatProvider } from "./provider";

const AGENT_PATH = [".github", "agents", "bipolar-orquestador.agent.md"];

async function exists(uri: vscode.Uri): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

async function confirmOverwrite(): Promise<boolean> {
  const answer = await vscode.window.showWarningMessage(
    "Ya existe bipolar-orquestador.agent.md en este workspace. ¿Sobrescribirlo?",
    { modal: true },
    "Sobrescribir",
  );
  return answer === "Sobrescribir";
}

async function installOrchestratorAgent(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    await vscode.window.showErrorMessage("Abre una carpeta para instalar el agente orquestador.");
    return;
  }
  const target = vscode.Uri.joinPath(folder.uri, ...AGENT_PATH);
  if ((await exists(target)) && !(await confirmOverwrite())) {
    return;
  }
  await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(target, ".."));
  await vscode.workspace.fs.writeFile(target, new TextEncoder().encode(ORCHESTRATOR_AGENT));
  await vscode.window.showTextDocument(target);
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Bipolar");
  const sync = new CopilotOverridesSync(context, output);
  const mcp = new BipolarMcpProvider(context, output);
  const provider = new BipolarChatProvider(
    output,
    (models) => sync.sync(models),
    (cfg) => {
      mcp.setConfig(cfg).catch(() => undefined);
    },
  );
  context.subscriptions.push(
    output,
    provider,
    mcp,
    vscode.lm.registerLanguageModelChatProvider("bipolar", provider),
    vscode.lm.registerMcpServerDefinitionProvider("bipolar.delegation", mcp),
    vscode.commands.registerCommand("bipolar.refreshModels", () => provider.refresh()),
    vscode.commands.registerCommand("bipolar.installOrchestratorAgent", installOrchestratorAgent),
  );
}

export function deactivate(): void {}
