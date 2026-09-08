import { expect, test } from "bun:test";
import { ToolWaitState } from "../src/tool-state.js";

const calls = [{ local_call_id: "call-1", tool_name: "fixture.tool", arguments: {}, argument_error: null }] as const;

test("tool wait requires an exact ordered result batch before terminal settlement", () => {
  const state = new ToolWaitState();
  state.open("g", 1, 1, calls);
  expect(() => state.assertTerminalAllowed()).toThrow("pending_tool_results");
  expect(() => state.consume({ generation_id: "g", epoch: 1, round: 2, results: [{ call_id: "call-1" }] })).toThrow("illegal_tool_results");
  expect(() => state.consume({ generation_id: "g", epoch: 1, round: 1, results: [] })).toThrow("result_set_mismatch");
  expect(() => state.consume({ generation_id: "g", epoch: 1, round: 1, results: [{ call_id: "other" }] })).toThrow("result_set_mismatch");
  state.consume({ generation_id: "g", epoch: 1, round: 1, results: [{ call_id: "call-1" }] });
  state.assertTerminalAllowed();
});
