export const ROLE_USER = 1;
export const ROLE_ASSISTANT = 2;

export type AnthropicContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; source: { type: "base64"; media_type: string; data: string } }
  | { type: "tool_use"; id: string; name: string; input: object }
  | { type: "tool_result"; tool_use_id: string; content: string };

export interface AnthropicMessage {
  role: "user" | "assistant";
  content: AnthropicContentBlock[];
}

export interface AnthropicTool {
  name: string;
  description: string;
  input_schema: object;
}

interface ChatMessageLike {
  role: number;
  content: readonly unknown[];
}

interface ToolLike {
  name: string;
  description: string;
  inputSchema?: object;
}

type Raw = Record<string, unknown>;

function isRecord(value: unknown): value is Raw {
  return typeof value === "object" && value !== null;
}

function isTextPart(part: unknown): part is { value: string } {
  return isRecord(part) && typeof part.value === "string";
}

function isToolCallPart(part: unknown): part is { callId: string; name: string; input: object } {
  return (
    isRecord(part) &&
    typeof part.callId === "string" &&
    typeof part.name === "string" &&
    isRecord(part.input)
  );
}

function isToolResultPart(part: unknown): part is { callId: string; content: unknown[] } {
  return (
    isRecord(part) &&
    typeof part.callId === "string" &&
    Array.isArray(part.content) &&
    !("name" in part)
  );
}

function isImagePart(part: unknown): part is { mimeType: string; data: Uint8Array } {
  return (
    isRecord(part) &&
    typeof part.mimeType === "string" &&
    part.mimeType.startsWith("image/") &&
    part.data instanceof Uint8Array
  );
}

function isTextDataPart(part: unknown): part is { mimeType: string; data: Uint8Array } {
  return (
    isRecord(part) &&
    typeof part.mimeType === "string" &&
    (part.mimeType.startsWith("text/") || part.mimeType.includes("json")) &&
    part.data instanceof Uint8Array
  );
}

function resultPartText(part: unknown): string {
  if (isTextPart(part)) {
    return part.value;
  }
  if (isRecord(part) && part.value !== undefined) {
    return JSON.stringify(part.value);
  }
  if (isTextDataPart(part)) {
    return Buffer.from(part.data).toString("utf8");
  }
  return "";
}

function toolResultText(content: readonly unknown[]): string {
  return content
    .map(resultPartText)
    .filter((text) => text !== "")
    .join("\n");
}

function toBlock(part: unknown): AnthropicContentBlock | undefined {
  if (isToolCallPart(part)) {
    return { type: "tool_use", id: part.callId, name: part.name, input: part.input };
  }
  if (isToolResultPart(part)) {
    return { type: "tool_result", tool_use_id: part.callId, content: toolResultText(part.content) };
  }
  if (isImagePart(part)) {
    const data = Buffer.from(part.data).toString("base64");
    return { type: "image", source: { type: "base64", media_type: part.mimeType, data } };
  }
  if (isTextPart(part) && part.value !== "") {
    return { type: "text", text: part.value };
  }
  return undefined;
}

function toRole(role: number): AnthropicMessage["role"] {
  return role === ROLE_ASSISTANT ? "assistant" : "user";
}

function toMessage(message: ChatMessageLike): AnthropicMessage {
  const content = message.content
    .map(toBlock)
    .filter((block): block is AnthropicContentBlock => block !== undefined);
  return { role: toRole(message.role), content };
}

function appendMerged(merged: AnthropicMessage[], message: AnthropicMessage): AnthropicMessage[] {
  const last = merged[merged.length - 1];
  if (last !== undefined && last.role === message.role) {
    last.content.push(...message.content);
  } else {
    merged.push(message);
  }
  return merged;
}

// Anthropic exige que los tool_result vayan antes que cualquier otro bloque del turno de usuario.
function toolResultsFirst(message: AnthropicMessage): AnthropicMessage {
  if (message.role !== "user") {
    return message;
  }
  const results = message.content.filter((block) => block.type === "tool_result");
  const others = message.content.filter((block) => block.type !== "tool_result");
  return { role: message.role, content: [...results, ...others] };
}

export function toAnthropicMessages(messages: readonly ChatMessageLike[]): AnthropicMessage[] {
  return messages
    .map(toMessage)
    .filter((message) => message.content.length > 0)
    .reduce(appendMerged, [])
    .map(toolResultsFirst);
}

export function toAnthropicTools(tools?: readonly ToolLike[]): AnthropicTool[] | undefined {
  if (tools === undefined || tools.length === 0) {
    return undefined;
  }
  return tools.map((tool) => ({
    name: tool.name,
    description: tool.description,
    input_schema: tool.inputSchema ?? { type: "object", properties: {} },
  }));
}

export function buildRequestBody(
  model: { model: string; maxOutputTokens: number },
  messages: AnthropicMessage[],
  tools: AnthropicTool[] | undefined,
  toolMode?: number,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    model: model.model,
    max_tokens: model.maxOutputTokens,
    messages,
    stream: true,
  };
  if (tools === undefined) {
    return body;
  }
  body.tools = tools;
  if (toolMode === 2) {
    body.tool_choice = { type: "any" };
  }
  return body;
}
