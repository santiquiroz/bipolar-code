import { test } from "node:test";
import assert from "node:assert/strict";
import { ORCHESTRATOR_AGENT } from "../agentTemplate";
import { mcpUrl } from "../catalog";

test("mcpUrl agrega /mcp a la URL base", () => {
  assert.equal(mcpUrl("http://h:8000"), "http://h:8000/mcp");
});

test("mcpUrl quita las barras finales", () => {
  assert.equal(mcpUrl("http://h:8000//"), "http://h:8000/mcp");
});

test("ORCHESTRATOR_AGENT tiene frontmatter con tools y description", () => {
  assert.ok(ORCHESTRATOR_AGENT.startsWith("---"));
  assert.ok(ORCHESTRATOR_AGENT.includes("tools: ['bipolar/*'"));
  assert.ok(ORCHESTRATOR_AGENT.includes("description:"));
});

test("ORCHESTRATOR_AGENT menciona job_status y verify", () => {
  assert.ok(ORCHESTRATOR_AGENT.includes("job_status"));
  assert.ok(ORCHESTRATOR_AGENT.includes("verify"));
});
