import * as vscode from "vscode";
import {
  BipolarConfig,
  BipolarModelInfo,
  catalogUrl,
  messagesUrl,
  normalizeConfig,
  parseCatalog,
  toModelInfo,
} from "./catalog";
import { buildRequestBody, toAnthropicMessages, toAnthropicTools } from "./convert";
import { AnthropicSseParser, StreamEvent } from "./sse";

const CATALOG_TIMEOUT_MS = 10_000;

type Progress = vscode.Progress<vscode.LanguageModelResponsePart>;

function abortOnCancel(token: vscode.CancellationToken): AbortController {
  const controller = new AbortController();
  token.onCancellationRequested(() => controller.abort());
  return controller;
}

function catalogErrorMessage(status: number): string {
  if (status === 401 || status === 403) {
    return "API key de bipolar inválida";
  }
  return `bipolar respondió ${status} al pedir el catálogo`;
}

function requestHeaders(model: BipolarModelInfo): Record<string, string> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "x-api-key": model.apiKey,
    "anthropic-version": "2023-06-01",
  };
  if (model.providerId !== null) {
    headers["X-Bipolar-Provider"] = model.providerId;
  }
  return headers;
}

async function errorMessageFrom(res: Response): Promise<string> {
  const fallback = `bipolar respondió ${res.status}`;
  try {
    const body = (await res.json()) as { error?: { message?: unknown } };
    const message = body.error?.message;
    return typeof message === "string" && message !== "" ? message : fallback;
  } catch {
    return fallback;
  }
}

function reportEvent(event: StreamEvent, progress: Progress): void {
  switch (event.kind) {
    case "text":
      progress.report(new vscode.LanguageModelTextPart(event.text));
      return;
    case "tool_call":
      progress.report(new vscode.LanguageModelToolCallPart(event.callId, event.name, event.input));
      return;
    case "error":
      throw new Error(event.message);
  }
}

async function streamEvents(body: ReadableStream<Uint8Array>, progress: Progress): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new AnthropicSseParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    parser.push(decoder.decode(value, { stream: true })).forEach((e) => reportEvent(e, progress));
  }
  parser.push(decoder.decode()).forEach((e) => reportEvent(e, progress));
  parser.end().forEach((e) => reportEvent(e, progress));
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function partLength(part: unknown): number {
  const value = (part as { value?: unknown } | null)?.value;
  return typeof value === "string" ? value.length : 0;
}

function countChars(text: string | vscode.LanguageModelChatRequestMessage): number {
  if (typeof text === "string") {
    return text.length;
  }
  return text.content.reduce((sum: number, part) => sum + partLength(part), 0);
}

export class BipolarChatProvider implements vscode.LanguageModelChatProvider<BipolarModelInfo> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeLanguageModelChatInformation = this.changed.event;

  constructor(
    private readonly output: vscode.OutputChannel,
    private readonly onModelsLoaded?: (models: BipolarModelInfo[]) => Promise<void>,
  ) {}

  refresh(): void {
    this.changed.fire();
  }

  dispose(): void {
    this.changed.dispose();
  }

  async provideLanguageModelChatInformation(
    options: vscode.PrepareLanguageModelChatModelOptions,
    token: vscode.CancellationToken,
  ): Promise<BipolarModelInfo[]> {
    // La configuración del grupo de providers llega en runtime pero @types/vscode no la declara.
    const config = normalizeConfig((options as { configuration?: unknown }).configuration);
    if (config === undefined) {
      return [];
    }
    const res = await this.fetchCatalog(config, token).catch(() => undefined);
    if (res === undefined) {
      return this.fail(`No se pudo conectar con bipolar en ${config.url}`, options.silent);
    }
    if (!res.ok) {
      return this.fail(catalogErrorMessage(res.status), options.silent);
    }
    const models = parseCatalog(await res.json()).map((entry) => toModelInfo(entry, config));
    this.output.appendLine(`Se cargaron ${models.length} modelos de bipolar`);
    this.notifyModelsLoaded(models);
    return models;
  }

  async provideLanguageModelChatResponse(
    model: BipolarModelInfo,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: Progress,
    token: vscode.CancellationToken,
  ): Promise<void> {
    try {
      await this.sendChat(model, messages, options, progress, token);
    } catch (error) {
      if (token.isCancellationRequested) {
        return;
      }
      this.output.appendLine(`Error en el chat con ${model.name}: ${errorText(error)}`);
      throw error;
    }
  }

  async provideTokenCount(
    _model: BipolarModelInfo,
    text: string | vscode.LanguageModelChatRequestMessage,
    _token: vscode.CancellationToken,
  ): Promise<number> {
    return Math.ceil(countChars(text) / 4);
  }

  private fetchCatalog(config: BipolarConfig, token: vscode.CancellationToken): Promise<Response> {
    const controller = abortOnCancel(token);
    const timer = setTimeout(() => controller.abort(), CATALOG_TIMEOUT_MS);
    return fetch(catalogUrl(config.url), {
      headers: { "x-api-key": config.apiKey },
      signal: controller.signal,
    }).finally(() => clearTimeout(timer));
  }

  private notifyModelsLoaded(models: BipolarModelInfo[]): void {
    this.onModelsLoaded?.(models).catch((error) =>
      this.output.appendLine(`No se pudieron ajustar los modelos de Copilot CLI: ${errorText(error)}`),
    );
  }

  private fail(message: string, silent: boolean): BipolarModelInfo[] {
    this.output.appendLine(message);
    if (silent) {
      return [];
    }
    throw new Error(message);
  }

  private async sendChat(
    model: BipolarModelInfo,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: Progress,
    token: vscode.CancellationToken,
  ): Promise<void> {
    const body = buildRequestBody(
      model,
      toAnthropicMessages(messages),
      toAnthropicTools(options.tools),
      options.toolMode,
    );
    const res = await fetch(messagesUrl(model.url), {
      method: "POST",
      headers: requestHeaders(model),
      body: JSON.stringify(body),
      signal: abortOnCancel(token).signal,
    });
    if (!res.ok) {
      throw new Error(await errorMessageFrom(res));
    }
    if (res.body !== null) {
      await streamEvents(res.body, progress);
    }
  }
}
