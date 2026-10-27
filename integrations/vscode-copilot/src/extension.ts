import * as vscode from "vscode";
import { CopilotOverridesSync } from "./copilotSettings";
import { BipolarChatProvider } from "./provider";

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Bipolar");
  const sync = new CopilotOverridesSync(context, output);
  const provider = new BipolarChatProvider(output, (models) => sync.sync(models));
  context.subscriptions.push(
    output,
    provider,
    vscode.lm.registerLanguageModelChatProvider("bipolar", provider),
    vscode.commands.registerCommand("bipolar.refreshModels", () => provider.refresh()),
  );
}

export function deactivate(): void {}
