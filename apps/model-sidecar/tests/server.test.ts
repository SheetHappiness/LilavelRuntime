import { describe, expect, test } from "bun:test";
import type { ApiKeyResolver, AssistantMessage, AssistantMessageEvent, Context, Model, SimpleStreamOptions } from "@oh-my-pi/pi-ai";
import {
  createModelSidecar,
  type AuthStorageLike,
  type SidecarEvent,
  type SidecarModel,
  type StreamSimple,
} from "../src/index.js";
import { PROTOCOL_LIMITS } from "../src/protocol.js";
import { ProtocolServer, type ProtocolOutput } from "../src/server.js";

const model = {
  id: "gpt-5.6-luna",
  provider: "openai-codex",
  api: "openai-codex-responses",
  baseUrl: "https://example.invalid",
} as SidecarModel;

function providerEvent(type: "text_delta" | "done", delta = ""): AssistantMessageEvent {
  if (type === "text_delta") return { type, contentIndex: 0, delta, partial: {} as AssistantMessage };
  return { type, reason: "stop", message: {} as AssistantMessage };
}

class FakeAuth implements AuthStorageLike {
  resolver(_provider: string, _options?: Parameters<AuthStorageLike["resolver"]>[1]): ApiKeyResolver {
    return async () => "test-bearer";
  }

  close(): void {}
}

function makeStream(
  factory: (context: Context, options?: SimpleStreamOptions) => AsyncIterable<AssistantMessageEvent>,
): StreamSimple {
  return ((_: Model<"openai-codex-responses">, context, options) => factory(context, options)) as StreamSimple;
}

class InputQueue {
  readonly stream: ReadableStream<Uint8Array>;
  readonly #encoder = new TextEncoder();
  #controller: ReadableStreamDefaultController<Uint8Array> | undefined;

  constructor() {
    this.stream = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.#controller = controller;
      },
    });
  }

  push(value: string): void {
    this.#controller?.enqueue(this.#encoder.encode(`${value}\n`));
  }
}

class CaptureOutput implements ProtocolOutput {
  readonly chunks: string[] = [];

  write(chunk: string): boolean {
    this.chunks.push(chunk);
    return true;
  }

  once(_event: "drain", listener: () => void): unknown {
    listener();
    return this;
  }

  frames(): Array<Record<string, unknown>> {
    return this.chunks.map((chunk) => JSON.parse(chunk) as Record<string, unknown>);
  }
}

function command(type: "generate" | "cancel", generationId: string, epoch: number, prompt?: string): string {
  if (type === "generate") {
    return JSON.stringify({ protocol_version: 2, type, generation_id: generationId, epoch, prompt });
  }
  return JSON.stringify({ protocol_version: 2, type, generation_id: generationId, epoch });
}

test("protocol server performs ready, genuine streaming, cancellation, recovery, and shutdown", async () => {
  const input = new InputQueue();
  const output = new CaptureOutput();
  const sidecarEvents: SidecarEvent[] = [];
  let cancelledCommandSent = false;
  let recoveryCommandSent = false;
  const server = new ProtocolServer({
    input: input.stream,
    output,
    createSidecar: (onEvent) =>
      createModelSidecar({
        auth: new FakeAuth(),
        model,
        stream: makeStream(async function* (context, options) {
          const prompt = context.messages[0]?.content;
          if (typeof prompt !== "string" || prompt === "cancel") {
            yield providerEvent("text_delta", "before-cancel");
            await new Promise<void>((resolve) => {
              if (options?.signal?.aborted) resolve();
              else options?.signal?.addEventListener("abort", () => resolve(), { once: true });
            });
            return;
          }
          yield providerEvent("text_delta", "recovered");
          yield providerEvent("done");
        }),
        onEvent: (event) => {
          sidecarEvents.push(event);
          onEvent(event);
          if (event.type === "text_delta" && event.delta === "before-cancel" && !cancelledCommandSent) {
            cancelledCommandSent = true;
            input.push(command("cancel", event.requestId ?? "", event.epoch ?? 0));
          }
          if (event.type === "cancelled" && !recoveryCommandSent) {
            recoveryCommandSent = true;
            input.push(command("generate", "recovery", (event.epoch ?? 0) + 1, "recovery"));
          }
          if (event.type === "completed" && event.requestId === "recovery") {
            input.push(JSON.stringify({ protocol_version: 2, type: "shutdown" }));
          }
        },
      }),
  });

  input.push(command("generate", "first", 1, "cancel"));
  const exitCode = await server.run();
  const frames = output.frames();

  expect(exitCode).toBe(0);
  expect(frames.map((frame) => frame.type)).toEqual([
    "ready",
    "accepted",
    "text_delta",
    "cancelled",
    "accepted",
    "text_delta",
    "completed",
    "shutdown",
  ]);
  expect(frames.filter((frame) => frame.type === "text_delta").map((frame) => frame.delta)).toEqual([
    "before-cancel",
    "recovered",
  ]);
  expect(frames.filter((frame) => frame.type === "accepted").map((frame) => frame.generation_id)).toEqual([
    "first",
    "recovery",
  ]);
  expect(
    sidecarEvents
      .filter((event) => event.requestId !== undefined)
      .map((event) => ({ requestId: event.requestId, epoch: event.epoch })),
  ).toEqual([
    { requestId: "first", epoch: 1 },
    { requestId: "first", epoch: 1 },
    { requestId: "first", epoch: 1 },
    { requestId: "first", epoch: 1 },
    { requestId: "first", epoch: 1 },
    { requestId: "recovery", epoch: 2 },
    { requestId: "recovery", epoch: 2 },
    { requestId: "recovery", epoch: 2 },
    { requestId: "recovery", epoch: 2 },
    { requestId: "recovery", epoch: 2 },
  ]);
});

test("protocol server forwards structured context to the provider adapter", async () => {
  const input = new InputQueue();
  const output = new CaptureOutput();
  let observedContext: Context | undefined;
  const server = new ProtocolServer({
    input: input.stream,
    output,
    createSidecar: (onEvent) =>
      createModelSidecar({
        auth: new FakeAuth(),
        model,
        stream: makeStream(async function* (context) {
          observedContext = context;
          yield providerEvent("text_delta", "structured");
          yield providerEvent("done");
        }),
        onEvent: (event) => {
          onEvent(event);
          if (event.type === "completed") input.push(JSON.stringify({ protocol_version: 2, type: "shutdown" }));
        },
      }),
  });

  input.push(
    JSON.stringify({
      protocol_version: 2,
      type: "generate",
      generation_id: "structured",
      epoch: 1,
      messages: [
        { role: "user", text: "Remember the word quartz." },
        { role: "assistant", text: "ACK_1" },
        { role: "user", text: "What was the word?" },
      ],
    }),
  );

  expect(await server.run()).toBe(0);
  expect(observedContext?.messages).toHaveLength(3);
  expect(observedContext?.messages[1]).toMatchObject({
    role: "assistant",
    content: [{ type: "text", text: "ACK_1" }],
    api: "openai-codex-responses",
    provider: "openai-codex",
    model: "gpt-5.6-luna",
  });
  expect(output.frames().map((frame) => frame.type)).toEqual([
    "ready",
    "accepted",
    "text_delta",
    "completed",
    "shutdown",
  ]);
});

test("protocol server reports malformed input without hanging or corrupting stdout", async () => {
  const input = new InputQueue();
  const output = new CaptureOutput();
  const server = new ProtocolServer({
    input: input.stream,
    output,
    createSidecar: (onEvent) => createModelSidecar({ auth: new FakeAuth(), model, onEvent }),
  });
  input.push("not-json");

  const exitCode = await server.run();
  expect(exitCode).toBe(1);
  expect(output.frames()).toEqual([
    { protocol_version: 2, type: "ready", provider: "openai-codex", model_id: "gpt-5.6-luna", api: "openai-codex-responses" },
    { protocol_version: 2, type: "failed", code: "protocol_error" },
  ]);
});

test("protocol server fails startup when the sidecar omits the ready handshake", async () => {
  const output = new CaptureOutput();
  const server = new ProtocolServer({
    input: new InputQueue().stream,
    output,
    createSidecar: () =>
      createModelSidecar({
        auth: new FakeAuth(),
        model,
        onEvent: () => undefined,
      }),
  });

  expect(await server.run()).toBe(1);
  expect(output.frames()).toEqual([{ protocol_version: 2, type: "failed", code: "startup_error" }]);
});

test("protocol server emits one cleanup failure and exits when cancellation poisons the sidecar", async () => {
  const input = new InputQueue();
  const output = new CaptureOutput();
  let cancelSent = false;
  const server = new ProtocolServer({
    input: input.stream,
    output,
    createSidecar: (onEvent) =>
      createModelSidecar({
        auth: new FakeAuth(),
        model,
        cleanupTimeoutMs: 10,
        stream: ((_: Model<"openai-codex-responses">, _context, _options) => ({
          async next(): Promise<IteratorResult<AssistantMessageEvent>> {
            return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
          },
          async return(): Promise<IteratorResult<AssistantMessageEvent>> {
            return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
          },
          [Symbol.asyncIterator](): AsyncIterator<AssistantMessageEvent> {
            return this as unknown as AsyncIterator<AssistantMessageEvent>;
          },
        })) as StreamSimple,
        onEvent: (event) => {
          onEvent(event);
          if (event.type === "request_received" && !cancelSent) {
            cancelSent = true;
            input.push(command("cancel", event.requestId ?? "", event.epoch ?? 0));
          }
        },
      }),
  });

  input.push(command("generate", "poisoned", 1, "hang"));
  const exitCode = await server.run();
  const frames = output.frames();

  expect(exitCode).toBe(1);
  expect(frames.filter((frame) => frame.type === "failed")).toEqual([
    { protocol_version: 2, type: "failed", generation_id: "poisoned", epoch: 1, code: "cleanup_timeout" },
  ]);
  expect(frames.filter((frame) => frame.type === "cancelled")).toHaveLength(0);
});

test("defers output-limit terminal failure until provider cleanup completes", async () => {
  const input = new InputQueue();
  const output = new CaptureOutput();
  let releaseCleanup: (() => void) | undefined;
  const cleanupGate = new Promise<void>((resolve) => {
    releaseCleanup = resolve;
  });
  const server = new ProtocolServer({
    input: input.stream,
    output,
    createSidecar: (onEvent) =>
      createModelSidecar({
        auth: new FakeAuth(),
        model,
        stream: makeStream(async function* (_context, options) {
          try {
            yield providerEvent("text_delta", "x".repeat(PROTOCOL_LIMITS.maxResponseBytes));
            yield providerEvent("text_delta", "overflow");
          } finally {
            input.push(JSON.stringify({ protocol_version: 2, type: "health" }));
            await cleanupGate;
          }
        }),
        onEvent: (event) => {
          onEvent(event);
          if (event.type === "cancelled") {
            input.push(JSON.stringify({ protocol_version: 2, type: "shutdown" }));
          }
        },
      }),
  });

  input.push(command("generate", "limited", 1, "limit"));
  const releaseTimer = setTimeout(() => releaseCleanup?.(), 15);
  const exitCode = await server.run();
  clearTimeout(releaseTimer);
  const frames = output.frames();
  const healthIndex = frames.findIndex((frame) => frame.type === "health");
  const failedIndex = frames.findIndex((frame) => frame.type === "failed");

  expect(exitCode).toBe(0);
  expect(healthIndex).toBeGreaterThan(-1);
  expect(frames[healthIndex]).toMatchObject({ protocol_version: 2, type: "health", state: "busy" });
  expect(failedIndex).toBeGreaterThan(-1);
  expect(frames.filter((frame) => frame.type === "failed")).toEqual([
    { protocol_version: 2, type: "failed", generation_id: "limited", epoch: 1, code: "output_limit" },
  ]);
  expect(frames.filter((frame) => frame.type === "cancelled")).toHaveLength(0);
});

test("settles an active generation before forced shutdown timeout", async () => {
  const input = new InputQueue();
  const output = new CaptureOutput();
  const exitCalls: number[] = [];
  const server = new ProtocolServer({
    input: input.stream,
    output,
    shutdownTimeoutMs: 10,
    createSidecar: (onEvent) =>
      createModelSidecar({
        auth: new FakeAuth(),
        model,
        cleanupTimeoutMs: 100,
        stream: ((_: Model<"openai-codex-responses">, _context, _options) => ({
          async next(): Promise<IteratorResult<AssistantMessageEvent>> {
            return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
          },
          async return(): Promise<IteratorResult<AssistantMessageEvent>> {
            return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
          },
          [Symbol.asyncIterator](): AsyncIterator<AssistantMessageEvent> {
            return this as unknown as AsyncIterator<AssistantMessageEvent>;
          },
        })) as StreamSimple,
        onEvent,
      }),
    forceExit: (code) => exitCalls.push(code),
  });

  input.push(command("generate", "shutdown-race", 1, "hang"));
  input.push(JSON.stringify({ protocol_version: 2, type: "shutdown" }));
  const exitCode = await server.run();
  const frames = output.frames();

  expect(exitCode).toBe(1);
  expect(exitCalls).toEqual([1]);
  expect(frames.filter((frame) => frame.type === "failed")).toEqual([
    { protocol_version: 2, type: "failed", generation_id: "shutdown-race", epoch: 1, code: "shutdown_timeout" },
  ]);
  expect(frames.filter((frame) => frame.type === "shutdown")).toHaveLength(0);
});

test("does not acknowledge clean shutdown after cleanup poisons the sidecar", async () => {
  const input = new InputQueue();
  const output = new CaptureOutput();
  const server = new ProtocolServer({
    input: input.stream,
    output,
    shutdownTimeoutMs: 100,
    createSidecar: (onEvent) =>
      createModelSidecar({
        auth: new FakeAuth(),
        model,
        cleanupTimeoutMs: 10,
        stream: ((_: Model<"openai-codex-responses">, _context, _options) => ({
          async next(): Promise<IteratorResult<AssistantMessageEvent>> {
            return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
          },
          async return(): Promise<IteratorResult<AssistantMessageEvent>> {
            return await new Promise<IteratorResult<AssistantMessageEvent>>(() => undefined);
          },
          [Symbol.asyncIterator](): AsyncIterator<AssistantMessageEvent> {
            return this as unknown as AsyncIterator<AssistantMessageEvent>;
          },
        })) as StreamSimple,
        onEvent,
      }),
  });

  input.push(command("generate", "cleanup-shutdown", 1, "hang"));
  input.push(JSON.stringify({ protocol_version: 2, type: "shutdown" }));
  const exitCode = await server.run();
  const frames = output.frames();

  expect(exitCode).toBe(1);
  expect(frames.filter((frame) => frame.type === "failed")).toEqual([
    {
      protocol_version: 2,
      type: "failed",
      generation_id: "cleanup-shutdown",
      epoch: 1,
      code: "cleanup_timeout",
    },
  ]);
  expect(frames.filter((frame) => frame.type === "shutdown")).toHaveLength(0);
});
