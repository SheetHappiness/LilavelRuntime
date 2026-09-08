import { describe, expect, test } from "bun:test";
import type { RawSseEvent, ToolCall as PiToolCall } from "@oh-my-pi/pi-ai";
import {
  finalizeOrdinaryFunctionCalls,
  GenerationCallMap,
  mapTools,
  RawFunctionCallCollector,
  ToolTransportError,
} from "../src/tool-transport.js";

const specs = [{ name: "discord.send_message", description: "Send", input_schema: { type: "object" } }] as const;

function raw(item: object): RawSseEvent {
  return { event: "response.output_item.done", data: JSON.stringify({ type: "response.output_item.done", item }), raw: [] };
}

function call(id = "provider-call|provider-item", args: Record<string, unknown> = { body: "hello" }): PiToolCall {
  return { type: "toolCall", id, name: "discord_send_message", arguments: args };
}

function collectorWith(item: object): RawFunctionCallCollector {
  const collector = new RawFunctionCallCollector();
  collector.observe(raw(item));
  return collector;
}

describe("pinned pi-ai ordinary function raw argument proof", () => {
  test("valid raw JSON object must match pi-ai finalized call", () => {
    const { applicationByProviderAlias } = mapTools(specs);
    const collector = collectorWith({ type: "function_call", call_id: "provider-call", id: "provider-item", name: "discord_send_message", arguments: '{"body":"hello"}' });
    expect(finalizeOrdinaryFunctionCalls([call()], collector, new GenerationCallMap(), applicationByProviderAlias)).toEqual([
      { local_call_id: "call-1", tool_name: "discord.send_message", arguments: { body: "hello" }, argument_error: null },
    ]);
  });

  test.each(["{\"body\":", "{\"body\": \"hello\""]) ("malformed or truncated raw input is invalid_json", (rawArguments) => {
    const { applicationByProviderAlias } = mapTools(specs);
    const collector = collectorWith({ type: "function_call", call_id: "provider-call", id: "provider-item", name: "discord_send_message", arguments: rawArguments });
    expect(finalizeOrdinaryFunctionCalls([call("provider-call|provider-item", { body: "hello" })], collector, new GenerationCallMap(), applicationByProviderAlias)[0]?.argument_error).toBe("invalid_json");
  });

  test.each(["[]", "\"text\"", "null"]) ("raw JSON %s is invalid_shape", (rawArguments) => {
    const { applicationByProviderAlias } = mapTools(specs);
    const collector = collectorWith({ type: "function_call", call_id: "provider-call", id: "provider-item", name: "discord_send_message", arguments: rawArguments });
    expect(finalizeOrdinaryFunctionCalls([call("provider-call|provider-item", {})], collector, new GenerationCallMap(), applicationByProviderAlias)[0]?.argument_error).toBe("invalid_shape");
  });

  test("repaired normalized arguments, missing raw correspondence, and ambiguous native IDs fail closed", () => {
    const { applicationByProviderAlias } = mapTools(specs);
    const repaired = collectorWith({ type: "function_call", call_id: "provider-call", id: "provider-item", name: "discord_send_message", arguments: '{"body":"raw"}' });
    expect(() => finalizeOrdinaryFunctionCalls([call("provider-call|provider-item", { body: "repaired" })], repaired, new GenerationCallMap(), applicationByProviderAlias)).toThrow(ToolTransportError);
    expect(() => finalizeOrdinaryFunctionCalls([call()], new RawFunctionCallCollector(), new GenerationCallMap(), applicationByProviderAlias)).toThrow(ToolTransportError);
    const duplicate = collectorWith({ type: "function_call", call_id: "provider-call", id: "provider-item", name: "discord_send_message", arguments: "{}" });
    expect(() => duplicate.observe(raw({ type: "function_call", call_id: "provider-call", id: "provider-item", name: "discord_send_message", arguments: "{}" }))).toThrow(ToolTransportError);
  });

  test("stable batch order, generation-local IDs, and alias collisions are enforced", () => {
    const mapped = mapTools(specs);
    const collector = new RawFunctionCallCollector();
    collector.observe(raw({ type: "function_call", call_id: "very-long-provider-id", id: "one", name: "discord_send_message", arguments: "{}" }));
    collector.observe(raw({ type: "function_call", call_id: "very-long-provider-id", id: "two", name: "discord_send_message", arguments: "{}" }));
    expect(finalizeOrdinaryFunctionCalls([call("very-long-provider-id|one", {}), call("very-long-provider-id|two", {})], collector, new GenerationCallMap(), mapped.applicationByProviderAlias).map((item) => item.local_call_id)).toEqual(["call-1", "call-2"]);
    expect(() => mapTools([{ name: "a.b", description: "x", input_schema: {} }, { name: "a_b", description: "y", input_schema: {} }])).toThrow(ToolTransportError);
  });
});
