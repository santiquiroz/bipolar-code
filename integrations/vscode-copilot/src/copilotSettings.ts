import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { BipolarModelInfo } from "./catalog";
import {
  DEFAULT_GROUP,
  OVERRIDES_KEY,
  OVERRIDES_SECTION,
  bipolarGroupNames,
  desiredOverrides,
  mergeOverrides,
} from "./overrides";

const MANAGED_KEYS_STATE = "bipolar.managedOverrideKeys";

function isEnabled(): boolean {
  return vscode.workspace.getConfiguration("bipolar").get<boolean>("manageCopilotOverrides", true);
}

function currentOverrides(): Record<string, unknown> {
  const config = vscode.workspace.getConfiguration(OVERRIDES_SECTION);
  const value = config.inspect<unknown>(OVERRIDES_KEY)?.globalValue;
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export class CopilotOverridesSync {
  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  async sync(models: readonly BipolarModelInfo[]): Promise<void> {
    if (!isEnabled()) {
      return;
    }
    const groups = await this.readGroups();
    const managedBefore = this.context.globalState.get<string[]>(MANAGED_KEYS_STATE, []);
    const result = mergeOverrides(currentOverrides(), desiredOverrides(groups, models), managedBefore);
    if (result.changed) {
      await this.write(result.next, result.managed.length);
    }
    await this.context.globalState.update(MANAGED_KEYS_STATE, result.managed);
  }

  private async readGroups(): Promise<string[]> {
    try {
      const text = await fs.promises.readFile(this.languageModelsFile(), "utf8");
      return bipolarGroupNames(JSON.parse(text));
    } catch {
      return [DEFAULT_GROUP];
    }
  }

  // globalStorageUri es <User>/globalStorage/<extension-id>.
  private languageModelsFile(): string {
    const userDir = path.dirname(path.dirname(this.context.globalStorageUri.fsPath));
    return path.join(userDir, "chatLanguageModels.json");
  }

  private async write(next: Record<string, unknown>, count: number): Promise<void> {
    const config = vscode.workspace.getConfiguration(OVERRIDES_SECTION);
    await config.update(OVERRIDES_KEY, next, vscode.ConfigurationTarget.Global);
    this.output.appendLine(
      `Bipolar ajustó ${count} modelos en ${OVERRIDES_SECTION}.${OVERRIDES_KEY} para que Copilot CLI cargue las herramientas bajo demanda.`,
    );
  }
}
