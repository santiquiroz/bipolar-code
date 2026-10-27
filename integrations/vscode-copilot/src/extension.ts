import * as vscode from "vscode";
import { BipolarChatProvider } from "./provider";

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Bipolar");
  const provider = new BipolarChatProvider(output);
  context.subscriptions.push(
    output,
    provider,
    vscode.lm.registerLanguageModelChatProvider("bipolar", provider),
    vscode.commands.registerCommand("bipolar.refreshModels", () => provider.refresh()),
  );
}

export function deactivate(): void {}
