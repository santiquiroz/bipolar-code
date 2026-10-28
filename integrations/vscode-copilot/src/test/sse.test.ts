import { test } from "node:test";
import assert from "node:assert/strict";
import { AnthropicSseParser, StreamEvent } from "../sse";

function line(data: object): string {
  return "data: " + JSON.stringify(data) + "\n";
}

function toolStart(index: number, id: string, name: string): string {
  return line({ type: "content_block_start", index, content_block: { type: "tool_use", id, name, input: {} } });
}

function jsonDelta(index: number, partial: string): string {
  return line({ type: "content_block_delta", index, delta: { type: "input_json_delta", partial_json: partial } });
}

function stop(index: number): string {
  return line({ type: "content_block_stop", index });
}

function feed(parser: AnthropicSseParser, chunks: string[]): StreamEvent[] {
  return chunks.flatMap((chunk) => parser.push(chunk)).concat(parser.end());
}

test("texto partido a mitad de línea", () => {
  const full = "event: content_block_delta\n" +
    line({ type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "hola mundo" } });
  const parser = new AnthropicSseParser();
  const events = feed(parser, [full.slice(0, 40), full.slice(40)]);
  assert.deepEqual(events, [{ kind: "text", text: "hola mundo" }]);
});

test("tool call con partial_json en tres fragmentos", () => {
  const parser = new AnthropicSseParser();
  const before = feed(parser, [
    toolStart(0, "t1", "read"),
    jsonDelta(0, '{"pa'),
    jsonDelta(0, 'th":"a'),
    jsonDelta(0, '.txt"}'),
  ]);
  assert.deepEqual(before, []);
  assert.deepEqual(parser.push(stop(0)), [
    { kind: "tool_call", callId: "t1", name: "read", input: { path: "a.txt" } },
  ]);
});

test("dos tool calls intercaladas", () => {
  const events = feed(new AnthropicSseParser(), [
    toolStart(1, "a", "read"),
    toolStart(2, "b", "write"),
    jsonDelta(1, '{"x":'),
    jsonDelta(2, '{"y":'),
    jsonDelta(1, "1}"),
    jsonDelta(2, "2}"),
    stop(1),
    stop(2),
  ]);
  assert.deepEqual(events, [
    { kind: "tool_call", callId: "a", name: "read", input: { x: 1 } },
    { kind: "tool_call", callId: "b", name: "write", input: { y: 2 } },
  ]);
});

test("evento error", () => {
  const events = feed(new AnthropicSseParser(), [
    line({ type: "error", error: { type: "overloaded_error", message: "overloaded" } }),
  ]);
  assert.deepEqual(events, [{ kind: "error", message: "overloaded" }]);
});

test("tool_use sin input_json_delta da input vacío", () => {
  const events = feed(new AnthropicSseParser(), [toolStart(0, "t1", "list"), stop(0)]);
  assert.deepEqual(events, [{ kind: "tool_call", callId: "t1", name: "list", input: {} }]);
});

test("end procesa la última línea sin salto final", () => {
  const parser = new AnthropicSseParser();
  const last = line({ type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "fin" } });
  assert.deepEqual(parser.push(last.trimEnd()), []);
  assert.deepEqual(parser.end(), [{ kind: "text", text: "fin" }]);
});
