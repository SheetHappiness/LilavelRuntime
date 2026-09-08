import { afterEach, describe, expect, test } from "bun:test";
import type { ApiKeyResolver, AssistantMessage, AssistantMessageEvent, Context, Model, SimpleStreamOptions } from "@oh-my-pi/pi-ai";
import {
  createModelSidecar,
  toPiAiContext,
  type AuthStorageLike,
  type SidecarEvent,
  type SidecarModel,
  type StreamSimple,
} from "../src/index.js";
import { PROTOCOL_LIMITS, type ContextMessage } from "../src/protocol.js";

const model = {
  id: "gpt-5.6-luna",
  provider: "openai-codex",
  api: "openai-codex-responses",
  baseUrl: "https://example.invalid",
} as SidecarModel;

function event(type: "start" | "text_delta" | "done", delta = ""): AssistantMessageEvent {
  if (type === "text_delta") return { type, contentIndex: 0, delta, partial: {} as AssistantMessage };
  if (type === "done") return { type, reason: "stop", message: {} as AssistantMessage };
  return { type, partial: {} as AssistantMessage };
}

class FakeAuth implements AuthStorageLike {
  resolverCalls: Array<{ provider: string; options?: Parameters<AuthStorageLike["resolver"]>[1] }> = [];
  resolverContexts: Array<{ lastChance: boolean; error: unknown }> = [];
  closed = false;

  resolver(provider: string, options?: Parameters<AuthStorageLike["resolver"]>[1]): ApiKeyResolver {
    this.resolverCalls.push({ provider, options });
    return async (context) => {
      this.resolverContexts.push({ lastChance: context.lastChance, error: context.error });
      return "test-bearer";
    };
  }

  close() {
    this.closed = true;
  }
}

function makeStream(factory: (context: Context, options?: SimpleStreamOptions) => AsyncIterable<AssistantMessageEvent>): StreamSimple {
  return ((_: Model<"openai-codex-responses">, context, options) => factory(context, options)) as StreamSimple;
}

const openHandles: Array<{ close: () => Promise<void> }> = [];
afterEach(async () => {
  while (openHandles.length) await openHandles.pop()?.close();
});

describe("ModelSidecar", () => {
  test("adapts provider-neutral context without exposing provider fields to the caller", () => {
    const messages: ContextMessage[] = [
      { role: "user", text: "Remember the word quartz." },
      { role: "assistant", text: "ACK_1" },
      { role: "user", text: "What was the word?" },
    ];

    const context = toPiAiContext(messages);

    expect(context.messages).toHaveLength(3);
    expect(context.messages[0]).toMatchObject({ role: "user", content: "Remember the word quartz." });
    expect(context.messages[1]).toMatchObject({
      role: "assistant",
      content: [{ type: "text", text: "ACK_1" }],
      api: "openai-codex-responses",
      provider: "openai-codex",
      model: "gpt-5.6-luna",
      stopReason: "stop",
    });
    expect(context.messages[1]).not.toHaveProperty("providerPayload");
    expect(context.messages[1]).not.toHaveProperty("responseId");
    expect(context.tools).toBeUndefined();
  });

  test("sends structured context to streamSimple without provider continuation state", async () => {
    let observedContext: Context | undefined;
    let observedOptions: SimpleStreamOptions | undefined;
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* (context, options) {
        observedContext = context;
        observedOptions = options;
        yield event("text_delta", "structured");
        yield event("done");
      }),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate([
      { role: "user", text: "Remember the word quartz." },
      { role: "assistant", text: "ACK_1" },
      { role: "user", text: "What was the word?" },
    ]);
    await expect(handle.result).resolves.toMatchObject({ status: "completed", text: "structured" });

    expect(observedContext?.messages).toHaveLength(3);
    expect(observedContext?.messages[2]).toMatchObject({ role: "user", content: "What was the word?" });
    expect(observedOptions?.sessionId).toBeUndefined();
    expect(observedOptions?.providerSessionState).toBeUndefined();
    expect(observedOptions?.statefulResponses).toBeUndefined();
  });

  test("maps trusted guidance exactly to Context.systemPrompt", async () => {
    let observedContext: Context | undefined;
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* (context) {
        observedContext = context;
        yield event("text_delta", "guided");
        yield event("done");
      }),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate({
      messages: [{ role: "user", text: "hello" }],
      system_prompt: ["trusted identity", "trusted behavior"],
    });
    await expect(handle.result).resolves.toMatchObject({ status: "completed", text: "guided" });

    expect(observedContext?.systemPrompt).toEqual(["trusted identity", "trusted behavior"]);
    expect(observedContext?.messages).toEqual([
      expect.objectContaining({ role: "user", content: "hello" }),
    ]);
    expect(observedContext?.tools).toBeUndefined();
  });

  test("enforces the protocol response bound inside the provider adapter", async () => {
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* () {
        yield event("text_delta", "x".repeat(PROTOCOL_LIMITS.maxResponseBytes));
        yield event("text_delta", "y");
        yield event("done");
      }),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("bounded");
    await expect(handle.result).rejects.toMatchObject({ code: "response_limit" });
    expect(sidecar.state).toBe("ready");
  });

  test("loads and validates the exact bundled Luna model", async () => {
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      stream: makeStream(async function* () {
        yield event("done");
      }),
    });

    await sidecar.start();
    expect(sidecar.model).toMatchObject({
      id: "gpt-5.6-luna",
      provider: "openai-codex",
      api: "openai-codex-responses",
    });
    await sidecar.close();
  });

  test("starts, correlates one request, and closes auth", async () => {
    const auth = new FakeAuth();
    const events: SidecarEvent[] = [];
    const sidecar = createModelSidecar({
      auth,
      model,
      stream: makeStream(async function* () {
        yield event("start");
        yield event("text_delta", "first");
        yield event("text_delta", " second");
        yield event("done");
      }),
      onEvent: (item) => events.push(item),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("private prompt must not be in events");
    const result = await handle.result;

    expect(result).toEqual({ requestId: handle.requestId, status: "completed", text: "first second" });
    expect(events.map((item) => item.type)).toEqual([
      "ready",
      "request_received",
      "provider_dispatch",
      "provider_first_event",
      "text_delta",
      "text_delta",
      "completed",
    ]);
    expect(new Set(events.filter((item) => item.requestId).map((item) => item.requestId))).toEqual(new Set([handle.requestId]));
    expect(events.find((item) => item.type === "text_delta" && item.delta === "first")).toBeDefined();

    await sidecar.close();
    expect(auth.closed).toBe(true);
  });

  test("uses the Core generation identity as the sidecar request alias", async () => {
    const events: SidecarEvent[] = [];
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* () {
        yield event("text_delta", "safe-delta");
        yield event("done");
      }),
      onEvent: (item) => events.push(item),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("ignored by the evidence projection", {
      generationId: "core-generation-1",
      epoch: 7,
    });
    await expect(handle.result).resolves.toMatchObject({
      requestId: "core-generation-1",
      status: "completed",
    });

    expect(handle.generationId).toBe("core-generation-1");
    expect(handle.epoch).toBe(7);
    expect(
      events
        .filter((item) => item.requestId !== undefined)
        .map((item) => ({ requestId: item.requestId, epoch: item.epoch })),
    ).toEqual([
      { requestId: "core-generation-1", epoch: 7 },
      { requestId: "core-generation-1", epoch: 7 },
      { requestId: "core-generation-1", epoch: 7 },
      { requestId: "core-generation-1", epoch: 7 },
      { requestId: "core-generation-1", epoch: 7 },
    ]);
  });

  test("passes genuine deltas before completion instead of buffering final text", async () => {
    let releaseSecond: (() => void) | undefined;
    const second = new Promise<void>((resolve) => {
      releaseSecond = resolve;
    });
    const observed: SidecarEvent[] = [];
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* () {
        yield event("text_delta", "now");
        await second;
        yield event("text_delta", " later");
        yield event("done");
      }),
      onEvent: (item) => observed.push(item),
    });
    openHandles.push(sidecar);
    await sidecar.start();
    const handle = sidecar.generate("streaming");

    while (!observed.some((item) => item.type === "text_delta" && item.delta === "now")) await Promise.resolve();
    expect(observed.some((item) => item.type === "completed")).toBe(false);
    expect(observed.filter((item) => item.type === "text_delta").map((item) => item.delta)).toEqual(["now"]);

    releaseSecond?.();
    await handle.result;
    expect(observed.filter((item) => item.type === "text_delta").map((item) => item.delta)).toEqual(["now", " later"]);
  });

  test("cancels the active request and recovers ready state", async () => {
    let attempts = 0;
    let releaseCleanup: (() => void) | undefined;
    let cleanupFinished = false;
    const cleanupGate = new Promise<void>((resolve) => {
      releaseCleanup = resolve;
    });
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* (_context, options) {
        if (attempts++ === 0) {
          try {
            await new Promise<void>((resolve) => options?.signal?.addEventListener("abort", () => resolve(), { once: true }));
            return;
          } finally {
            await cleanupGate;
            cleanupFinished = true;
          }
        }
        yield event("text_delta", "recovered");
        yield event("done");
      }),
    });
    openHandles.push(sidecar);
    await sidecar.start();
    const cancelled = sidecar.generate("cancel me");
    expect(sidecar.cancel(cancelled.requestId)).toBe(true);
    await Promise.resolve();
    expect(sidecar.state).toBe("busy");
    releaseCleanup?.();
    await expect(cancelled.result).resolves.toMatchObject({ status: "cancelled", text: "" });
    expect(cleanupFinished).toBe(true);
    expect(sidecar.state).toBe("ready");

    const retry = sidecar.generate("try again");
    await expect(retry.result).resolves.toMatchObject({ status: "completed", text: "recovered" });
    expect(sidecar.state).toBe("ready");
  });

  test("keeps cancellation bounded and poisons the sidecar when iterator cleanup hangs", async () => {
    const events: SidecarEvent[] = [];
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      cleanupTimeoutMs: 10,
      stream: makeStream(() => ({
        async next(): Promise<IteratorResult<AssistantMessageEvent>> {
          return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
        },
        async return(): Promise<IteratorResult<AssistantMessageEvent>> {
          return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
        },
        [Symbol.asyncIterator](): AsyncIterator<AssistantMessageEvent> {
          return this as unknown as AsyncIterator<AssistantMessageEvent>;
        },
      })),
      onEvent: (item) => events.push(item),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("blocked cleanup");
    expect(sidecar.cancel(handle.requestId, handle.epoch)).toBe(true);

    await expect(handle.result).rejects.toMatchObject({ code: "cleanup_timeout" });
    expect(sidecar.state).toBe("failed");
    expect(events.filter((item) => item.requestId === handle.requestId && item.type === "error")).toHaveLength(1);
    expect(events.find((item) => item.requestId === handle.requestId && item.type === "error")).toMatchObject({
      code: "cleanup_timeout",
    });

    const retry = sidecar.generate("must not reuse poisoned sidecar");
    await expect(retry.result).rejects.toMatchObject({ code: "not_ready" });
    await expect(sidecar.start()).rejects.toMatchObject({ code: "not_ready" });
    await sidecar.close();
  });

  test("requires the provider result acknowledgement before declaring cleanup reusable", async () => {
    let releaseResult: (() => void) | undefined;
    const resultGate = new Promise<void>((resolve) => {
      releaseResult = resolve;
    });
    let returnCalls = 0;
    const stream = makeStream(() => {
      const iterable = {
        async next(): Promise<IteratorResult<AssistantMessageEvent>> {
          return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
        },
        async return(): Promise<IteratorResult<AssistantMessageEvent>> {
          returnCalls += 1;
          return { done: true, value: undefined };
        },
        [Symbol.asyncIterator](): AsyncIterator<AssistantMessageEvent> {
          return this as unknown as AsyncIterator<AssistantMessageEvent>;
        },
        result: async () => {
          await resultGate;
        },
      };
      return iterable;
    });
    const sidecar = createModelSidecar({ auth: new FakeAuth(), model, cleanupTimeoutMs: 10, stream });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("blocked producer acknowledgement");
    expect(sidecar.cancel(handle.requestId)).toBe(true);
    await expect(handle.result).rejects.toMatchObject({ code: "cleanup_timeout" });
    expect(returnCalls).toBe(1);
    expect(sidecar.state).toBe("failed");

    releaseResult?.();
  });

  test("poisons the sidecar when provider cleanup rejects", async () => {
    const events: SidecarEvent[] = [];
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      cleanupTimeoutMs: 100,
      stream: makeStream(() => ({
        async next(): Promise<IteratorResult<AssistantMessageEvent>> {
          return { done: false, value: event("text_delta", "partial") };
        },
        async return(): Promise<IteratorResult<AssistantMessageEvent>> {
          throw new Error("cleanup failed");
        },
        [Symbol.asyncIterator](): AsyncIterator<AssistantMessageEvent> {
          return this as unknown as AsyncIterator<AssistantMessageEvent>;
        },
      })),
      onEvent: (item) => events.push(item),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("cleanup rejection");
    expect(sidecar.cancel(handle.requestId)).toBe(true);
    await expect(handle.result).rejects.toMatchObject({ code: "cleanup_error" });
    expect(sidecar.state).toBe("failed");
    expect(events.filter((item) => item.type === "error" && item.requestId === handle.requestId)).toHaveLength(1);
  });

  test("installs active request before request notification so an immediate cancel is honored", async () => {
    const events: SidecarEvent[] = [];
    let sidecar: ReturnType<typeof createModelSidecar>;
    sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* () {
        yield event("text_delta", "should be discarded after cancel");
        yield event("done");
      }),
      onEvent: (item) => {
        events.push(item);
        if (item.type === "request_received") sidecar.cancel(item.requestId ?? "", item.epoch);
      },
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("cancel from observer");
    await expect(handle.result).resolves.toMatchObject({ status: "cancelled" });
    expect(events.map((item) => item.type)).toContain("cancelled");
    expect(events.filter((item) => item.type === "completed")).toHaveLength(0);
  });

  test("uses the auth resolver boundary and omits tools and provider session state", async () => {
    const auth = new FakeAuth();
    let capturedContext: Context | undefined;
    let capturedOptions: SimpleStreamOptions | undefined;
    const sidecar = createModelSidecar({
      auth,
      model,
      stream: makeStream(async function* (context, options) {
        capturedContext = context;
        capturedOptions = options;
        if (!options) throw new Error("test stream options missing");
        const optionsForTest = options;
        const resolver = optionsForTest?.apiKey;
        if (typeof resolver === "function") {
          await resolver({ lastChance: false, error: undefined, signal: optionsForTest.signal });
          await resolver({ lastChance: true, error: new Error("simulated auth failure"), signal: optionsForTest.signal });
        }
        yield event("text_delta", "ok");
        yield event("done");
      }),
    });
    openHandles.push(sidecar);
    await sidecar.start();
    const handle = sidecar.generate("auth boundary");
    await handle.result;

    expect(Object.hasOwn(capturedContext ?? {}, "tools")).toBe(false);
    expect(capturedContext?.messages).toHaveLength(1);
    expect(Object.hasOwn(capturedOptions ?? {}, "statefulResponses")).toBe(false);
    expect(Object.hasOwn(capturedOptions ?? {}, "sessionId")).toBe(false);
    expect(Object.hasOwn(capturedOptions ?? {}, "providerSessionState")).toBe(false);
    expect(capturedOptions?.preferWebsockets).toBe(false);
    expect(auth.resolverCalls).toEqual([
      { provider: "openai-codex", options: { sessionId: handle.requestId, modelId: "gpt-5.6-luna", baseUrl: "https://example.invalid" } },
    ]);
    expect(auth.resolverContexts.map(({ lastChance }) => lastChance)).toEqual([false, true]);
  });

  test("records latency marks in causal order", async () => {
    let now = 100;
    const observed: SidecarEvent[] = [];
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      now: () => now,
      stream: makeStream(async function* () {
        now = 120;
        yield event("text_delta", "timed");
        now = 140;
        yield event("done");
      }),
      onEvent: (item) => observed.push(item),
    });
    openHandles.push(sidecar);
    await sidecar.start();
    await sidecar.generate("latency").result;

    const finalEvent = observed.find((item) => item.type === "completed");
    expect(finalEvent?.metadata.marks).toMatchObject({
      requestReceived: 0,
      providerDispatch: 0,
      providerFirstEvent: 20,
      firstNonEmptyDelta: 20,
      completion: 40,
    });
    const marks = finalEvent?.metadata.marks;
    expect(marks?.requestReceived).toBeLessThanOrEqual(marks?.providerDispatch ?? -1);
    expect(marks?.providerDispatch).toBeLessThanOrEqual(marks?.providerFirstEvent ?? -1);
    expect(marks?.providerFirstEvent).toBeLessThanOrEqual(marks?.firstNonEmptyDelta ?? -1);
    expect(marks?.firstNonEmptyDelta).toBeLessThanOrEqual(marks?.completion ?? -1);
  });
});
