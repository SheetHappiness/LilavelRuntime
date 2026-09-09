import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { parseV3Command, parseV3Event } from "../src/protocol-v3.js";

const corpus = JSON.parse(readFileSync(new URL("../../../testdata/protocol-v3/tool-cases.json", import.meta.url), "utf8")) as { cases: { surface: string; frame: string; accepted: boolean }[] };
for (const entry of corpus.cases) test(`V3 shared corpus ${entry.frame.slice(0, 24)}`, () => {
  const parser = entry.surface === "command" ? parseV3Command : parseV3Event;
  if (entry.accepted) expect(() => parser(entry.frame)).not.toThrow(); else expect(() => parser(entry.frame)).toThrow();
});

test("V3 rejects duplicate keys instead of accepting JSON normalization", () => {
  expect(() => parseV3Command('{"protocol_version":3,"type":"health","type":"shutdown"}')).toThrow("duplicate_field");
});

test("V3 covers structured generate and lifecycle commands/events", () => {
  expect(parseV3Command('{"protocol_version":3,"type":"generate","generation_id":"g","epoch":1,"messages":[{"role":"user","text":"hello"}],"system_prompt":["trusted"]}').type).toBe("generate");
  expect(parseV3Command('{"protocol_version":3,"type":"cancel","generation_id":"g","epoch":1}').type).toBe("cancel");
  expect(parseV3Command('{"protocol_version":3,"type":"health"}').type).toBe("health");
  expect(parseV3Command('{"protocol_version":3,"type":"shutdown"}').type).toBe("shutdown");
  expect(parseV3Event('{"protocol_version":3,"type":"completed","generation_id":"g","epoch":1}').type).toBe("completed");
  expect(parseV3Event('{"protocol_version":3,"type":"cancelled","generation_id":"g","epoch":1}').type).toBe("cancelled");
});
