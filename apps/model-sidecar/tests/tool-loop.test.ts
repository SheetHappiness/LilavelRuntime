import { expect, test } from "bun:test";
import type { AssistantMessage, RawSseEvent, ToolCall } from "@oh-my-pi/pi-ai";

import { ToolLoopSidecar, type ProviderTurn } from "../src/tool-loop.js";
import type { V3Event, V3ToolResult } from "../src/protocol-v3.js";

const spec = { name: "fake.echo", description: "Echo", input_schema: { type: "object" } } as const;
const result = (callId: string): V3ToolResult => ({
  call_id: callId,
  status: "ok",
  reason_code: null,
  output: { fixture: "ok" },
  effect: "none",
});

function usage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function assistant(content: AssistantMessage["content"], stopReason: AssistantMessage["stopReason"]): AssistantMessage {
  return {
    role: "assistant",
    content,
    api: "openai-codex-responses",
    provider: "openai-codex",
    model: "gpt-5.6-luna",
    usage: usage(),
    stopReason,
    timestamp: Date.now(),
  };
}

function raw(callId: string, itemId: string, name: string, argumentsText: string): RawSseEvent {
  return {
    event: "response.output_item.done",
    data: JSON.stringify({
      type: "response.output_item.done",
      item: { type: "function_call", call_id: callId, id: itemId, name, arguments: argumentsText },
    }),
    raw: [],
  };
}

function toolTurn(providerId: string, value: string, afterCleanup: () => void = () => undefined): ProviderTurn {
  return async (_context, _signal, hooks) => {
    const [callId, itemId] = providerId.split("|");
    const call: ToolCall = { type: "toolCall", id: providerId, name: "fake_echo", arguments: { value } };
    hooks.onSseEvent(raw(callId!, itemId!, call.name, JSON.stringify(call.arguments)));
    hooks.onEvent({ type: "toolcall_end", contentIndex: 0, toolCall: call, partial: assistant([call], "toolUse") });
    const message = assistant([call], "toolUse");
    hooks.onEvent({ type: "done", reason: "toolUse", message });
    afterCleanup();
    return message;
  };
}

function finalTurn(text: string, inspect: (messageCount: number, toolCallId: string) => void = () => undefined): ProviderTurn {
  return async (context, _signal, hooks) => {
    const last = context.messages.at(-1);
    inspect(context.messages.length, last?.role === "toolResult" ? last.toolCallId : "");
    hooks.onEvent({ type: "text_delta", contentIndex: 0, delta: text, partial: assistant([{ type: "text", text }], "stop") });
    const message = assistant([{ type: "text", text }], "stop");
    hooks.onEvent({ type: "done", reason: "stop", message });
    return message;
  };
}

test("one generation continues after exact tool results and emits tool_calls only after cleanup", async () => {
  const events: V3Event[] = [];
  let turn = 0;
  let cleaned = false;
  const first = toolTurn("provider-1|item-1", "one", () => { cleaned = true; });
  const second = finalTurn("after", (count, providerId) => {
    expect(count).toBe(3);
    expect(providerId).toBe("provider-1|item-1");
  });
  const loop = new ToolLoopSidecar({
    runProviderTurn: async (...args) => (++turn === 1 ? first(...args) : second(...args)),
    emit: (event) => {
      if (event.type === "tool_calls") expect(cleaned).toBeTrue();
      events.push(event);
    },
  });
  loop.start({ protocol_version: 3, type: "generate", generation_id: "g", epoch: 1, prompt: "p", tools: [spec] });
  await until(() => events.some((event) => event.type === "tool_calls"));
  const calls = events.find((event): event is Extract<V3Event, { type: "tool_calls" }> => event.type === "tool_calls")!;
  loop.submit({ protocol_version: 3, type: "tool_results", generation_id: "g", epoch: 1, round: 1, results: [result(calls.calls[0]!.local_call_id)] });
  await loop.waitIdle();
  expect(events.map((event) => event.type)).toEqual(["accepted", "tool_calls", "text_delta", "completed"]);
});

test("generation call map persists across rounds while each provider turn gets fresh raw collection", async () => {
  const events: V3Event[] = [];
  const turns = [toolTurn("p1|i1", "one"), toolTurn("p2|i2", "two"), finalTurn("done")];
  let index = 0;
  const loop = new ToolLoopSidecar({ runProviderTurn: (...args) => turns[index++]!(...args), emit: (event) => events.push(event) });
  loop.start({ protocol_version: 3, type: "generate", generation_id: "g", epoch: 7, messages: [{ role: "user", text: "p" }], system_prompt: ["trusted"], tools: [spec] });
  await until(() => events.filter((event) => event.type === "tool_calls").length === 1);
  loop.submit({ protocol_version: 3, type: "tool_results", generation_id: "g", epoch: 7, round: 1, results: [result("call-1")] });
  await until(() => events.filter((event) => event.type === "tool_calls").length === 2);
  loop.submit({ protocol_version: 3, type: "tool_results", generation_id: "g", epoch: 7, round: 2, results: [result("call-2")] });
  await loop.waitIdle();
  const batches = events.filter((event): event is Extract<V3Event, { type: "tool_calls" }> => event.type === "tool_calls");
  expect(batches.map((event) => [event.round, event.calls[0]!.local_call_id])).toEqual([[1, "call-1"], [2, "call-2"]]);
  expect(events.at(-1)?.type).toBe("completed");
});

test("cancellation is terminal only after provider turn cleanup resolves", async () => {
  const events: V3Event[] = [];
  let cleanupConfirmed = false;
  const provider: ProviderTurn = async (_context, signal) => {
    await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
    await Promise.resolve();
    cleanupConfirmed = true;
    throw new Error("cancelled");
  };
  const loop = new ToolLoopSidecar({
    runProviderTurn: provider,
    emit: (event) => {
      if (event.type === "cancelled") expect(cleanupConfirmed).toBeTrue();
      events.push(event);
    },
  });
  loop.start({ protocol_version: 3, type: "generate", generation_id: "g", epoch: 1, prompt: "p" });
  expect(loop.cancel("g", 1)).toBeTrue();
  await loop.waitIdle();
  expect(events.map((event) => event.type)).toEqual(["accepted", "cancelled"]);
});

test("cleanup failure outranks cancellation and permanently poisons reuse", async () => {
  const events: V3Event[] = [];
  const provider: ProviderTurn = async (_context, signal) => {
    await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
    throw new Error("cleanup_timeout");
  };
  const loop = new ToolLoopSidecar({ runProviderTurn: provider, emit: (event) => events.push(event) });
  const command = { protocol_version: 3, type: "generate", generation_id: "g", epoch: 1, prompt: "p" } as const;
  loop.start(command);
  expect(loop.cancel("g", 1)).toBeTrue();
  await loop.waitIdle();
  expect(events.map((event) => event.type)).toEqual(["accepted", "failed"]);
  expect(events.at(-1)).toMatchObject({ type: "failed", code: "cleanup_timeout" });
  expect(() => loop.start({ ...command, generation_id: "next", epoch: 2 })).toThrow("closed");
});

test("wrong result order, second pending batch, and terminal during tool_wait fail closed", async () => {
  const events: V3Event[] = [];
  const loop = new ToolLoopSidecar({ runProviderTurn: toolTurn("p|i", "one"), emit: (event) => events.push(event) });
  loop.start({ protocol_version: 3, type: "generate", generation_id: "g", epoch: 1, prompt: "p", tools: [spec] });
  await until(() => events.some((event) => event.type === "tool_calls"));
  expect(() => loop.submit({ protocol_version: 3, type: "tool_results", generation_id: "g", epoch: 1, round: 2, results: [result("call-1")] })).toThrow("illegal_tool_results");
  expect(() => loop.submit({ protocol_version: 3, type: "tool_results", generation_id: "g", epoch: 1, round: 1, results: [result("other")] })).toThrow("result_set_mismatch");
  loop.close();
  await loop.waitIdle();
  expect(events.at(-1)?.type).toBe("cancelled");
});

async function until(predicate: () => boolean): Promise<void> {
  const deadline = performance.now() + 1_000;
  while (performance.now() < deadline) {
    if (predicate()) return;
    await Bun.sleep(1);
  }
  throw new Error("condition timed out");
}
