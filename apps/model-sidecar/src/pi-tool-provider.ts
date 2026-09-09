import {
  streamSimple,
  type ApiKeyResolver,
  type AssistantMessage,
  type Context,
  type Model,
} from "@oh-my-pi/pi-ai";
import { discoverAuthStorage } from "@oh-my-pi/pi-ai/auth-broker";
import { getBundledModel } from "@oh-my-pi/pi-catalog";

import type { AuthStorageLike } from "./index.js";
import type { ProviderTurn, ProviderTurnHooks } from "./tool-loop.js";

const PROVIDER = "openai-codex";
const MODEL_ID = "gpt-5.6-luna";
const API = "openai-codex-responses";
const CLEANUP_TIMEOUT_MS = 5_000;

class CleanupTimeoutError extends Error {}

type ResultBearingStream = AsyncIterable<Parameters<ProviderTurnHooks["onEvent"]>[0]> & {
  result?: () => Promise<unknown>;
};

export interface PiToolProvider {
  readonly provider: string;
  readonly modelId: string;
  readonly api: string;
  readonly run: ProviderTurn;
  close(): void;
}

/** Build the inactive V3 provider boundary using supported auth discovery only. */
export async function createPiToolProvider(): Promise<PiToolProvider> {
  const auth: AuthStorageLike = await discoverAuthStorage();
  const model = getBundledModel<"openai-codex-responses">(PROVIDER, MODEL_ID);
  if (model.provider !== PROVIDER || model.id !== MODEL_ID || model.api !== API) {
    auth.close();
    throw new Error("startup_error");
  }

  const run: ProviderTurn = async (context, signal, hooks) =>
    runTurn(auth, model, context, signal, hooks);
  return {
    provider: PROVIDER,
    modelId: MODEL_ID,
    api: API,
    run,
    close: () => auth.close(),
  };
}

async function runTurn(
  auth: AuthStorageLike,
  model: Model<"openai-codex-responses">,
  context: Context,
  signal: AbortSignal,
  hooks: ProviderTurnHooks,
): Promise<AssistantMessage> {
  const apiKey: ApiKeyResolver = auth.resolver(PROVIDER, {
    modelId: model.id,
    baseUrl: model.baseUrl,
  });
  const stream = streamSimple(model, context, {
    apiKey,
    signal,
    preferWebsockets: false,
    onSseEvent: hooks.onSseEvent,
  }) as ResultBearingStream;
  const iterator = stream[Symbol.asyncIterator]();
  let message: AssistantMessage | undefined;
  let failure: unknown;
  try {
    while (true) {
      const item = await iterator.next();
      if (item.done) break;
      hooks.onEvent(item.value);
      if (item.value.type === "done") message = item.value.message;
      if (item.value.type === "error") throw new Error(item.value.reason);
    }
    if (signal.aborted) throw new Error("cancelled");
    if (!message) throw new Error("provider_error");
  } catch (error) {
    failure = error;
  } finally {
    const operations: Promise<unknown>[] = [];
    if (iterator.return) operations.push(Promise.resolve(iterator.return()));
    if (stream.result) operations.push(Promise.resolve(stream.result()));
    try {
      await withTimeout(Promise.all(operations), CLEANUP_TIMEOUT_MS);
    } catch (error) {
      throw new Error(error instanceof CleanupTimeoutError ? "cleanup_timeout" : "cleanup_error");
    }
  }
  if (failure) throw failure;
  return message as AssistantMessage;
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timer = setTimeout(() => reject(new CleanupTimeoutError("timeout")), timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
