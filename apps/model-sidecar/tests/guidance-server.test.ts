import { expect, test } from "bun:test";
import type { ApiKeyResolver, AssistantMessage, AssistantMessageEvent, Context, Model } from "@oh-my-pi/pi-ai";
import {
  createModelSidecar,
  type AuthStorageLike,
  type SidecarEvent,
  type SidecarModel,
  type StreamSimple,
} from "../src/index.js";
import { ProtocolServer, type ProtocolOutput } from "../src/server.js";

const model = {
  id: "gpt-5.6-luna",
  provider: "openai-codex",
  api: "openai-codex-responses",
  baseUrl: "https://example.invalid",
} as SidecarModel;

class InputQueue {
  #controller: ReadableStreamDefaultController<Uint8Array> | undefined;
  readonly stream = new ReadableStream<Uint8Array>({
    start: (controller) => {
      this.#controller = controller;
    },
  });

  push(line: string): void {
    this.#controller?.enqueue(new TextEncoder().encode(`${line}\n`));
  }
}

class CaptureOutput implements ProtocolOutput {
  readonly chunks: string[] = [];

  write(chunk: string): boolean {
    this.chunks.push(chunk);
    return true;
  }

  once(_event: "drain", listener: () => void): unknown {
    queueMicrotask(listener);
    return this;
  }

  frames(): Array<Record<string, unknown>> {
    return this.chunks.map((chunk) => JSON.parse(chunk) as Record<string, unknown>);
  }
}

class FakeAuth implements AuthStorageLike {
  resolver(_provider: string): ApiKeyResolver {
    return async () => "test-bearer";
  }

  close(): void {}
}

function providerEvent(type: "text_delta" | "done", delta = ""): AssistantMessageEvent {
  if (type === "text_delta") {
    return { type, contentIndex: 0, delta, partial: {} as AssistantMessage };
  }
  return { type, reason: "stop", message: {} as AssistantMessage };
}

function makeStream(
  factory: (context: Context) => AsyncIterable<AssistantMessageEvent>,
): StreamSimple {
  return ((_: Model<"openai-codex-responses">, context) => factory(context)) as StreamSimple;
}

test("protocol server carries trusted guidance from JSONL into Context.systemPrompt", async () => {
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
          yield providerEvent("text_delta", "guided");
          yield providerEvent("done");
        }),
        onEvent: (event: SidecarEvent) => {
          onEvent(event);
          if (event.type === "completed") {
            input.push(JSON.stringify({ protocol_version: 2, type: "shutdown" }));
          }
        },
      }),
  });

  input.push(
    JSON.stringify({
      protocol_version: 2,
      type: "generate",
      generation_id: "guided-wire",
      epoch: 1,
      messages: [{ role: "user", text: "hello" }],
      system_prompt: ["trusted identity", "trusted behavior"],
    }),
  );

  expect(await server.run()).toBe(0);
  expect(observedContext?.systemPrompt).toEqual(["trusted identity", "trusted behavior"]);
  expect(output.frames().map((frame) => frame.type)).toEqual([
    "ready",
    "accepted",
    "text_delta",
    "completed",
    "shutdown",
  ]);
});
