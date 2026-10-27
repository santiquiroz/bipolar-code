import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ROLE_ASSISTANT,
  ROLE_USER,
  buildRequestBody,
  toAnthropicMessages,
  toAnthropicTools,
} from "../convert";

const model = { model: "auto", maxOutputTokens: 16000 };

test("mensaje de texto del usuario", () => {
  const result = toAnthropicMessages([{ role: ROLE_USER, content: [{ value: "hola" }] }]);
  assert.deepEqual(result, [{ role: "user", content: [{ type: "text", text: "hola" }] }]);
});

test("tool call seguido de tool result conserva el par de ids", () => {
  const result = toAnthropicMessages([
    { role: ROLE_ASSISTANT, content: [{ callId: "c1", name: "read", input: { path: "a" } }] },
    { role: ROLE_USER, content: [{ callId: "c1", content: [{ value: "a" }, { value: "b" }] }] },
  ]);
  assert.deepEqual(result, [
    { role: "assistant", content: [{ type: "tool_use", id: "c1", name: "read", input: { path: "a" } }] },
    { role: "user", content: [{ type: "tool_result", tool_use_id: "c1", content: "a\nb" }] },
  ]);
});

test("dos mensajes de usuario seguidos se juntan", () => {
  const result = toAnthropicMessages([
    { role: ROLE_USER, content: [{ value: "uno" }] },
    { role: ROLE_USER, content: [{ value: "dos" }] },
  ]);
  assert.equal(result.length, 1);
  assert.deepEqual(result[0].content, [
    { type: "text", text: "uno" },
    { type: "text", text: "dos" },
  ]);
});

test("una imagen PNG sale en base64", () => {
  const part = { mimeType: "image/png", data: new Uint8Array([1, 2, 3]) };
  const result = toAnthropicMessages([{ role: ROLE_USER, content: [part] }]);
  assert.deepEqual(result[0].content, [
    { type: "image", source: { type: "base64", media_type: "image/png", data: "AQID" } },
  ]);
});

test("una parte cache_control se ignora", () => {
  const part = { mimeType: "cache_control", data: new Uint8Array([1]) };
  assert.deepEqual(toAnthropicMessages([{ role: ROLE_USER, content: [part] }]), []);
});

test("buildRequestBody agrega tool_choice solo con toolMode 2", () => {
  const tools = toAnthropicTools([{ name: "t", description: "d" }]);
  const required = buildRequestBody(model, [], tools, 2);
  const auto = buildRequestBody(model, [], tools, 1);
  assert.deepEqual(required.tool_choice, { type: "any" });
  assert.equal("tool_choice" in auto, false);
  assert.equal(required.stream, true);
  assert.equal(required.max_tokens, 16000);
});

test("toAnthropicTools usa el esquema por defecto y devuelve undefined si no hay", () => {
  assert.deepEqual(toAnthropicTools([{ name: "t", description: "d" }]), [
    { name: "t", description: "d", input_schema: { type: "object", properties: {} } },
  ]);
  assert.equal(toAnthropicTools([]), undefined);
});
