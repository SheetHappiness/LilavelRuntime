import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { parseV3Command, parseV3Event } from "../src/protocol-v3.js";

const corpus = JSON.parse(readFileSync(new URL("../../../testdata/protocol-v3/tool-cases.json", import.meta.url), "utf8")) as { cases: { surface: string; frame: string; accepted: boolean }[] };
for (const entry of corpus.cases) test(`V3 shared corpus ${entry.frame.slice(0, 24)}`, () => {
  const parser = entry.surface === "command" ? parseV3Command : parseV3Event;
  if (entry.accepted) expect(() => parser(entry.frame)).not.toThrow(); else expect(() => parser(entry.frame)).toThrow();
});
