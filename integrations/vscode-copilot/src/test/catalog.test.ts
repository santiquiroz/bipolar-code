import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CatalogEntry,
  catalogUrl,
  messagesUrl,
  normalizeConfig,
  parseCatalog,
  toModelInfo,
} from "../catalog";

const cfg = { url: "http://h:8000", apiKey: "k" };

const autoEntry: CatalogEntry = {
  id: "bipolar-auto",
  provider_id: null,
  model: "auto",
  name: "Bipolar Auto",
  max_input_tokens: 200000,
  max_output_tokens: 32000,
  tools: true,
  vision: false,
};

const llamacppEntry: CatalogEntry = {
  id: "llamacpp/qwen",
  provider_id: "llamacpp",
  model: "qwen",
  name: "llamacpp · qwen",
  max_input_tokens: 32768,
  max_output_tokens: 8192,
  tools: true,
  vision: false,
};

test("normalizeConfig usa la URL por defecto", () => {
  assert.deepEqual(normalizeConfig({ apiKey: "k" }), { url: "http://localhost:8000", apiKey: "k" });
});

test("normalizeConfig sin apiKey devuelve undefined", () => {
  assert.equal(normalizeConfig({ url: "x" }), undefined);
  assert.equal(normalizeConfig(undefined), undefined);
});

test("catalogUrl y messagesUrl quitan la barra final", () => {
  assert.equal(catalogUrl("http://h:8000/"), "http://h:8000/v1/bipolar/models");
  assert.equal(messagesUrl("http://h:8000"), "http://h:8000/v1/messages");
});

test("parseCatalog descarta entradas incompletas", () => {
  assert.deepEqual(parseCatalog({ data: [autoEntry, { id: "x" }] }), [autoEntry]);
});

test("parseCatalog sin data lanza error", () => {
  assert.throws(() => parseCatalog({}), { message: "Respuesta de catálogo inválida" });
});

test("toModelInfo de Auto usa la familia bipolar", () => {
  const info = toModelInfo(autoEntry, cfg);
  assert.equal(info.family, "bipolar");
  assert.equal(info.providerId, null);
  assert.equal(info.capabilities.toolCalling, true);
});

test("toModelInfo de un provider usa su id como familia", () => {
  const info = toModelInfo(llamacppEntry, cfg);
  assert.equal(info.family, "llamacpp");
  assert.equal(info.model, "qwen");
});
