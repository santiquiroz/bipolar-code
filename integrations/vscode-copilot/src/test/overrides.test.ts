import { test } from "node:test";
import assert from "node:assert/strict";
import {
  bipolarGroupNames,
  desiredOverride,
  desiredOverrides,
  mergeOverrides,
  overrideKeys,
} from "../overrides";

const models = [
  { id: "bipolar-auto", maxInputTokens: 200000 },
  { id: "llamacpp/qwen", maxInputTokens: 32768 },
];

test("bipolarGroupNames ignora los grupos de otros vendors", () => {
  const json = [
    { name: "Bipolar", vendor: "bipolar" },
    { name: "Ollama", vendor: "ollama-models" },
  ];
  assert.deepEqual(bipolarGroupNames(json), ["Bipolar"]);
});

test("bipolarGroupNames usa el grupo por defecto si el JSON no es válido", () => {
  assert.deepEqual(bipolarGroupNames({ nope: true }), ["Bipolar"]);
  assert.deepEqual(bipolarGroupNames(null), ["Bipolar"]);
  assert.deepEqual(bipolarGroupNames([{ name: "", vendor: "bipolar" }]), ["Bipolar"]);
});

test("bipolarGroupNames devuelve todos los grupos de bipolar", () => {
  const json = [
    { name: "Bipolar", vendor: "bipolar" },
    { name: "Bipolar remoto", vendor: "bipolar" },
  ];
  assert.deepEqual(bipolarGroupNames(json), ["Bipolar", "Bipolar remoto"]);
});

test("overrideKeys arma la clave exacta de cada grupo", () => {
  assert.deepEqual(overrideKeys(["A", "B"], "x/y"), ["bipolar/A/x/y", "bipolar/B/x/y"]);
});

test("desiredOverride con contexto grande solo cambia la familia", () => {
  assert.deepEqual(desiredOverride({ maxInputTokens: 128000 }), { family: "claude-sonnet-4.6" });
});

test("desiredOverride con contexto pequeño limita las herramientas", () => {
  assert.deepEqual(desiredOverride({ maxInputTokens: 32000 }), {
    family: "claude-sonnet-4.6",
    availableTools: ["builtin:*"],
  });
});

test("mergeOverrides agrega las claves nuevas y reporta el cambio", () => {
  const desired = desiredOverrides(["Bipolar"], models);
  const result = mergeOverrides({ other: { family: "x" } }, desired, []);
  assert.equal(result.changed, true);
  assert.deepEqual(result.managed, ["bipolar/Bipolar/bipolar-auto", "bipolar/Bipolar/llamacpp/qwen"]);
  assert.deepEqual(result.next["bipolar/Bipolar/bipolar-auto"], { family: "claude-sonnet-4.6" });
  assert.deepEqual(result.next.other, { family: "x" });
});

test("mergeOverrides nunca sobrescribe una clave del usuario", () => {
  const desired = desiredOverrides(["Bipolar"], models);
  const mine = { family: "gpt-5.5" };
  const result = mergeOverrides({ "bipolar/Bipolar/bipolar-auto": mine }, desired, []);
  assert.deepEqual(result.next["bipolar/Bipolar/bipolar-auto"], mine);
  assert.deepEqual(result.managed, ["bipolar/Bipolar/llamacpp/qwen"]);
});

test("mergeOverrides elimina las claves gestionadas que ya no se desean", () => {
  const current = { "bipolar/Bipolar/viejo": { family: "claude-sonnet-4.6" } };
  const desired = desiredOverrides(["Bipolar"], [models[0]]);
  const result = mergeOverrides(current, desired, ["bipolar/Bipolar/viejo"]);
  assert.equal("bipolar/Bipolar/viejo" in result.next, false);
  assert.deepEqual(result.managed, ["bipolar/Bipolar/bipolar-auto"]);
  assert.equal(result.changed, true);
});

test("mergeOverrides con las mismas entradas no cambia nada la segunda vez", () => {
  const desired = desiredOverrides(["Bipolar"], models);
  const first = mergeOverrides({}, desired, []);
  const second = mergeOverrides(first.next, desired, first.managed);
  assert.equal(second.changed, false);
  assert.deepEqual(second.managed, first.managed);
});

test("mergeOverrides no muta el objeto actual", () => {
  const current = { "bipolar/Bipolar/viejo": { family: "x" } };
  const snapshot = JSON.parse(JSON.stringify(current));
  mergeOverrides(current, desiredOverrides(["Bipolar"], models), ["bipolar/Bipolar/viejo"]);
  assert.deepEqual(current, snapshot);
});
