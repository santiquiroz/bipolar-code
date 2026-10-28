export interface CatalogEntry {
  id: string;
  provider_id: string | null;
  model: string;
  name: string;
  max_input_tokens: number;
  max_output_tokens: number;
  tools: boolean;
  vision: boolean;
}

export interface BipolarConfig {
  url: string;
  apiKey: string;
}

export interface BipolarModelInfo {
  id: string;
  name: string;
  family: string;
  version: string;
  maxInputTokens: number;
  maxOutputTokens: number;
  capabilities: { toolCalling: boolean; imageInput: boolean };
  providerId: string | null;
  model: string;
  url: string;
  apiKey: string;
}

export const DEFAULT_URL = "http://localhost:8000";

type Raw = Record<string, unknown>;

function isRecord(value: unknown): value is Raw {
  return typeof value === "object" && value !== null;
}

function nonEmptyString(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

function toNumber(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export function normalizeConfig(raw: unknown): BipolarConfig | undefined {
  if (!isRecord(raw)) {
    return undefined;
  }
  const apiKey = nonEmptyString(raw.apiKey);
  if (apiKey === undefined) {
    return undefined;
  }
  return { url: nonEmptyString(raw.url) ?? DEFAULT_URL, apiKey };
}

function stripTrailingSlashes(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, "");
}

export function catalogUrl(baseUrl: string): string {
  return stripTrailingSlashes(baseUrl) + "/v1/bipolar/models";
}

export function messagesUrl(baseUrl: string): string {
  return stripTrailingSlashes(baseUrl) + "/v1/messages";
}

function parseEntry(raw: unknown): CatalogEntry | undefined {
  if (!isRecord(raw)) {
    return undefined;
  }
  const id = nonEmptyString(raw.id);
  const model = nonEmptyString(raw.model);
  if (id === undefined || model === undefined) {
    return undefined;
  }
  return {
    id,
    provider_id: nonEmptyString(raw.provider_id) ?? null,
    model,
    name: nonEmptyString(raw.name) ?? id,
    max_input_tokens: toNumber(raw.max_input_tokens),
    max_output_tokens: toNumber(raw.max_output_tokens),
    tools: Boolean(raw.tools),
    vision: Boolean(raw.vision),
  };
}

export function parseCatalog(json: unknown): CatalogEntry[] {
  if (!isRecord(json) || !Array.isArray(json.data)) {
    throw new Error("Respuesta de catálogo inválida");
  }
  return json.data
    .map(parseEntry)
    .filter((entry): entry is CatalogEntry => entry !== undefined);
}

export function toModelInfo(entry: CatalogEntry, config: BipolarConfig): BipolarModelInfo {
  return {
    id: entry.id,
    name: entry.name,
    family: entry.provider_id ?? "bipolar",
    version: "1",
    maxInputTokens: entry.max_input_tokens,
    maxOutputTokens: entry.max_output_tokens,
    capabilities: { toolCalling: entry.tools, imageInput: entry.vision },
    providerId: entry.provider_id,
    model: entry.model,
    url: config.url,
    apiKey: config.apiKey,
  };
}
