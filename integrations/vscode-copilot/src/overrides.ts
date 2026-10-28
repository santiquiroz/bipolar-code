export const OVERRIDES_SECTION = "chat.agentHost.copilot";
export const OVERRIDES_KEY = "modelCapabilityOverrides";
export const TOOL_SEARCH_FAMILY = "claude-sonnet-4.6";
export const SMALL_CONTEXT_TOKENS = 64000;
export const DEFAULT_GROUP = "Bipolar";

type Raw = Record<string, unknown>;
type Override = Record<string, unknown>;

export interface MergeResult {
  next: Raw;
  managed: string[];
  changed: boolean;
}

function isRecord(value: unknown): value is Raw {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function bipolarGroupName(entry: unknown): string | undefined {
  if (!isRecord(entry) || entry.vendor !== "bipolar") {
    return undefined;
  }
  return typeof entry.name === "string" && entry.name.trim() !== "" ? entry.name : undefined;
}

export function bipolarGroupNames(json: unknown): string[] {
  if (!Array.isArray(json)) {
    return [DEFAULT_GROUP];
  }
  const names = json
    .map(bipolarGroupName)
    .filter((name): name is string => name !== undefined);
  return names.length > 0 ? names : [DEFAULT_GROUP];
}

export function overrideKeys(groups: readonly string[], modelId: string): string[] {
  return groups.map((group) => `bipolar/${group}/${modelId}`);
}

export function desiredOverride(model: { maxInputTokens: number }): Override {
  // Los modelos locales pequeños no caben ni con las herramientas MCP diferidas.
  if (model.maxInputTokens < SMALL_CONTEXT_TOKENS) {
    return { family: TOOL_SEARCH_FAMILY, availableTools: ["builtin:*"] };
  }
  return { family: TOOL_SEARCH_FAMILY };
}

export function desiredOverrides(
  groups: readonly string[],
  models: readonly { id: string; maxInputTokens: number }[],
): Record<string, Override> {
  const entries = models.flatMap((model) =>
    overrideKeys(groups, model.id).map((key): [string, Override] => [key, desiredOverride(model)]),
  );
  return Object.fromEntries(entries);
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortKeys);
  }
  if (!isRecord(value)) {
    return value;
  }
  const sorted = Object.keys(value)
    .sort()
    .map((key): [string, unknown] => [key, sortKeys(value[key])]);
  return Object.fromEntries(sorted);
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(sortKeys(a)) === JSON.stringify(sortKeys(b));
}

export function mergeOverrides(
  current: Raw,
  desired: Record<string, Override>,
  managedBefore: readonly string[],
): MergeResult {
  const owned = new Set(managedBefore);
  const next: Raw = { ...current };
  for (const key of owned) {
    if (!(key in desired)) {
      delete next[key];
    }
  }
  const managed = Object.keys(desired).filter((key) => !(key in current) || owned.has(key));
  for (const key of managed) {
    next[key] = desired[key];
  }
  return { next, managed, changed: !sameJson(current, next) };
}
