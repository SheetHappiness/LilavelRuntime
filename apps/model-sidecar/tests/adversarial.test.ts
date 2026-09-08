import { afterEach, describe, expect, test } from "bun:test";
import type {
  ApiKeyResolver,
  AssistantMessage,
  AssistantMessageEvent,
  Context,
  Model,
  SimpleStreamOptions,
} from "@oh-my-pi/pi-ai";
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

function providerEvent(type: "text_delta" | "done", delta = ""): AssistantMessageEvent {
  if (type === "text_delta") return { type, contentIndex: 0, delta, partial: {} as AssistantMessage };
  return { type, reason: "stop", message: {} as AssistantMessage };
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

class NeverDrainsOutput implements ProtocolOutput {
  readonly chunks: string[] = [];

  write(chunk: string): boolean {
    this.chunks.push(chunk);
    return false;
  }

  once(_event: "drain", _listener: () => void): unknown {
    // Deliberately emulate a dead stdout consumer. The writer deadline must
    // make the protocol host fail closed instead of leaving run() pending.
    return this;
  }
}

class BlocksAfterOutput implements ProtocolOutput {
  readonly chunks: string[] = [];
  #writes = 0;

  constructor(private readonly acceptedWrites: number) {}

  write(chunk: string): boolean {
    this.chunks.push(chunk);
    this.#writes += 1;
    return this.#writes <= this.acceptedWrites;
  }

  once(_event: "drain", _listener: () => void): unknown {
    // The terminal frame is accepted by write() but never gets a drain
    // notification, so the writer's bounded timeout is the only completion.
    return this;
  }
}

function command(type: "generate", generationId: string, epoch: number, prompt: string): string {
  return JSON.stringify({ protocol_version: 2, type, generation_id: generationId, epoch, prompt });
}

function shutdownCommand(): string {
  return JSON.stringify({ protocol_version: 2, type: "shutdown" });
}

const openHandles: Array<{ close: () => Promise<void> }> = [];
afterEach(async () => {
  while (openHandles.length) await openHandles.pop()?.close();
});

describe("adversarial lifecycle boundaries", () => {
  test("observes an iterator.next rejection when cancellation is already signalled", async () => {
    const unhandled: unknown[] = [];
    let nextObserverAttached = false;
    const onUnhandled = (reason: unknown): void => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandled);

    const observed: SidecarEvent[] = [];
    let rejectNext: ((reason?: unknown) => void) | undefined;
    let sidecar: ReturnType<typeof createModelSidecar>;
    sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(() => ({
        next: () => {
          const pending = new Promise<IteratorResult<AssistantMessageEvent>>((resolve, reject) => {
            void resolve;
            rejectNext = reject;
          });
          // Suppress the host-level unhandled-rejection report so this test
          // can inspect whether the sidecar itself observes the promise.
          void pending.catch(() => undefined);
          const originalThen = pending.then.bind(pending);
          const originalCatch = pending.catch.bind(pending);
          Object.defineProperty(pending, "then", {
            value: (...args: unknown[]) => {
              nextObserverAttached = true;
              return (originalThen as (...items: unknown[]) => unknown)(...args);
            },
          });
          Object.defineProperty(pending, "catch", {
            value: (...args: unknown[]) => {
              nextObserverAttached = true;
              return (originalCatch as (...items: unknown[]) => unknown)(...args);
            },
          });
          queueMicrotask(() => rejectNext?.(new Error("iterator.next failed after abort")));
          return pending;
        },
        return: async () => ({ done: true, value: undefined }),
        [Symbol.asyncIterator](): AsyncIterator<AssistantMessageEvent> {
          return this as unknown as AsyncIterator<AssistantMessageEvent>;
        },
      })),
      onEvent: (event) => {
        observed.push(event);
        if (event.type === "request_received") sidecar.cancel(event.requestId ?? "", event.epoch);
      },
    });
    openHandles.push(sidecar);

    try {
      await sidecar.start();
      const handle = sidecar.generate("cancel before first await");
      await expect(handle.result).resolves.toMatchObject({ status: "cancelled" });

      // Let the rejected iterator promise and the host's unhandled-rejection
      // turn run before asserting the cleanup observer was installed.
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      expect(unhandled).toEqual([]);
      expect(nextObserverAttached).toBe(true);
      expect(observed.filter((event) => event.type === "cancelled")).toHaveLength(1);
      expect(observed.filter((event) => event.type === "completed")).toHaveLength(0);
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
  });

  test("settles only once when provider races cancellation with completion", async () => {
    const observed: SidecarEvent[] = [];
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const sidecar = createModelSidecar({
      auth: new FakeAuth(),
      model,
      stream: makeStream(async function* (_context, options) {
        yield providerEvent("text_delta", "partial");
        await gate;
        if (options?.signal?.aborted) return;
        yield providerEvent("done");
      }),
      onEvent: (event) => observed.push(event),
    });
    openHandles.push(sidecar);

    await sidecar.start();
    const handle = sidecar.generate("race");
    const deltaDeadline = performance.now() + 1_000;
    while (!observed.some((event) => event.type === "text_delta") && performance.now() < deltaDeadline) {
      await Promise.resolve();
    }
    expect(observed.some((event) => event.type === "text_delta")).toBe(true);
    expect(sidecar.cancel(handle.requestId, handle.epoch)).toBe(true);
    release?.();

    await expect(handle.result).resolves.toMatchObject({ status: "cancelled", text: "partial" });
    expect(observed.filter((event) => event.type === "cancelled")).toHaveLength(1);
    expect(observed.filter((event) => event.type === "completed")).toHaveLength(0);
  });

  test("bounds a readiness write when stdout never reports drain", async () => {
    const output = new NeverDrainsOutput();
    const server = new ProtocolServer({
      input: new InputQueue().stream,
      output,
      writeTimeoutMs: 10,
      createSidecar: (onEvent) => {
        const sidecar = createModelSidecar({
          auth: new FakeAuth(),
          model,
          stream: makeStream(async function* () {
            yield providerEvent("done");
          }),
          onEvent,
        });
        openHandles.push(sidecar);
        return sidecar;
      },
    });

    const startedAt = performance.now();
    await expect(server.run()).resolves.toBe(1);
    expect(performance.now() - startedAt).toBeLessThan(1_000);
    expect(output.chunks).toHaveLength(1);
  });

  test("bounds a terminal write when stdout never reports drain", async () => {
    const input = new InputQueue();
    const output = new BlocksAfterOutput(2); // ready + accepted, then terminal blocks
    const server = new ProtocolServer({
      input: input.stream,
      output,
      writeTimeoutMs: 10,
      createSidecar: (onEvent) => {
        const sidecar = createModelSidecar({
          auth: new FakeAuth(),
          model,
          stream: makeStream(async function* () {
            yield providerEvent("done");
          }),
          onEvent: (event) => {
            onEvent(event);
            if (event.type === "completed") input.push(shutdownCommand());
          },
        });
        openHandles.push(sidecar);
        return sidecar;
      },
    });

    input.push(command("generate", "blocked-terminal", 1, "terminal"));
    const startedAt = performance.now();
    await expect(server.run()).resolves.toBe(1);
    expect(performance.now() - startedAt).toBeLessThan(1_000);
    expect(output.chunks).toHaveLength(3);
    expect(JSON.parse(output.chunks[2]) as Record<string, unknown>).toMatchObject({
      protocol_version: 2,
      type: "completed",
      generation_id: "blocked-terminal",
      epoch: 1,
    });
  });

  test("flushes every split delta and the terminal frame under synchronous output", async () => {
    const input = new InputQueue();
    const output = new CaptureOutput();
    const largeDelta = "x".repeat(PROTOCOL_LIMITS.maxDeltaBytes + 17);
    const server = new ProtocolServer({
      input: input.stream,
      output,
      createSidecar: (onEvent) => {
        const sidecar = createModelSidecar({
          auth: new FakeAuth(),
          model,
          stream: makeStream(async function* () {
            yield providerEvent("text_delta", largeDelta);
            yield providerEvent("done");
          }),
          onEvent: (event) => {
            onEvent(event);
            if (event.type === "completed") input.push(shutdownCommand());
          },
        });
        openHandles.push(sidecar);
        return sidecar;
      },
    });

    input.push(command("generate", "split-output", 1, "split"));
    await expect(server.run()).resolves.toBe(0);

    const frames = output.frames();
    expect(frames.map((frame) => frame.type)).toEqual([
      "ready",
      "accepted",
      "text_delta",
      "text_delta",
      "completed",
      "shutdown",
    ]);
    expect(frames.filter((frame) => frame.type === "text_delta").map((frame) => frame.delta).join(""))
      .toBe(largeDelta);
  });
});
