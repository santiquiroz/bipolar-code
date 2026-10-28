export type StreamEvent =
  | { kind: "text"; text: string }
  | { kind: "tool_call"; callId: string; name: string; input: object }
  | { kind: "error"; message: string };

interface ToolBuffer {
  id: string;
  name: string;
  json: string;
}

type Raw = Record<string, unknown>;

function isRecord(value: unknown): value is Raw {
  return typeof value === "object" && value !== null;
}

function parseData(line: string): Raw | undefined {
  if (!line.startsWith("data:")) {
    return undefined;
  }
  try {
    const parsed: unknown = JSON.parse(line.slice(5).trim());
    return isRecord(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function parseToolInput(json: string): object | undefined {
  try {
    const parsed: unknown = JSON.parse(json || "{}");
    return isRecord(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

export class AnthropicSseParser {
  private buffer = "";
  private readonly tools = new Map<number, ToolBuffer>();

  push(chunk: string): StreamEvent[] {
    this.buffer += chunk;
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() ?? "";
    return lines.flatMap((line) => this.processLine(line));
  }

  end(): StreamEvent[] {
    const rest = this.buffer;
    this.buffer = "";
    return this.processLine(rest);
  }

  private processLine(line: string): StreamEvent[] {
    const data = parseData(line.replace(/\r$/, ""));
    return data === undefined ? [] : this.processEvent(data);
  }

  private processEvent(data: Raw): StreamEvent[] {
    switch (data.type) {
      case "content_block_start":
        return this.startBlock(data);
      case "content_block_delta":
        return this.applyDelta(data);
      case "content_block_stop":
        return this.stopBlock(data);
      case "error":
        return [errorEvent(data)];
      default:
        return [];
    }
  }

  private startBlock(data: Raw): StreamEvent[] {
    const block = data.content_block;
    if (isRecord(block) && block.type === "tool_use") {
      this.tools.set(Number(data.index), { id: String(block.id), name: String(block.name), json: "" });
    }
    return [];
  }

  private applyDelta(data: Raw): StreamEvent[] {
    const delta = data.delta;
    if (!isRecord(delta)) {
      return [];
    }
    if (delta.type === "text_delta" && typeof delta.text === "string" && delta.text !== "") {
      return [{ kind: "text", text: delta.text }];
    }
    const tool = this.tools.get(Number(data.index));
    if (delta.type === "input_json_delta" && tool !== undefined) {
      tool.json += typeof delta.partial_json === "string" ? delta.partial_json : "";
    }
    return [];
  }

  private stopBlock(data: Raw): StreamEvent[] {
    const index = Number(data.index);
    const tool = this.tools.get(index);
    if (tool === undefined) {
      return [];
    }
    this.tools.delete(index);
    const input = parseToolInput(tool.json);
    if (input === undefined) {
      return [{ kind: "error", message: "JSON de herramienta inválido" }];
    }
    return [{ kind: "tool_call", callId: tool.id, name: tool.name, input }];
  }
}

function errorEvent(data: Raw): StreamEvent {
  const error = isRecord(data.error) ? data.error : {};
  const message = typeof error.message === "string" ? error.message : "Error de bipolar";
  return { kind: "error", message };
}
