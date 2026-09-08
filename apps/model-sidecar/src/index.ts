import { randomUUID } from "node:crypto";
import {
  streamSimple,
  type ApiKeyResolver,
  type AssistantMessageEvent,
  type Context,
  type Message,
  type Model,
  type SimpleStreamOptions,
  type Usage,
} from "@oh-my-pi/pi-ai";
import { discoverAuthStorage } from "@oh-my-pi/pi-ai/auth-broker";
import { getBundledModel } from "@oh-my-pi/pi-catalog";
import {
  validateContextMessages,
  validateEpoch,
  validateGenerationId,
  PROTOCOL_LIMITS,
  type ContextMessage,
  validateSystemPrompt,
} from "./protocol.js";

const PROVIDER = "openai-codex";
const MODEL_ID = "gpt-5.6-luna";
const API = "openai-codex-responses";

export type SidecarState = "new" | "starting" | "ready" | "busy" | "failed" | "closed";

export interface AuthStorageLike {
  resolver(
    provider: string,
    options?: { sessionId?: string; modelId?: string; baseUrl?: string },
  ): ApiKeyResolver;
  close(): void;
}

export type SidecarModel = Model<"openai-codex-responses">;

export type StreamSimple = (
  model: SidecarModel,
  context: Context,
  options?: SimpleStreamOptions,
) => AsyncIterable<AssistantMessageEvent>;

type StructuredModelInput = { messages: readonly ContextMessage[]; system_prompt?: readonly string[] };
export type ModelInput = string | readonly ContextMessage[] | StructuredModelInput;
function isStructuredInput(input: ModelInput): input is StructuredModelInput {
  return typeof input !== "string" && !Array.isArray(input) && "messages" in input;
}

export interface LatencyMarks {
  requestReceived?: number;
  providerDispatch?: number;
  providerFirstEvent?: number;
  firstNonEmptyDelta?: number;
  completion?: number;
  cancellation?: number;
}

export interface SidecarEventMetadata {
  marks: LatencyMarks;
  modelId?: string;
  provider?: string;
  api?: string;
}

interface SidecarEventBase {
  requestId?: string;
  epoch?: number;
  atMs: number;
  metadata: SidecarEventMetadata;
}

export type SidecarEvent =
  | (SidecarEventBase & { type: "ready" })
  | (SidecarEventBase & { type: "request_received" })
  | (SidecarEventBase & { type: "provider_dispatch" })
  | (SidecarEventBase & { type: "provider_first_event"; providerEventType: AssistantMessageEvent["type"] })
  | (SidecarEventBase & { type: "text_delta"; delta: string })
  | (SidecarEventBase & { type: "completed"; textLength: number })
  | (SidecarEventBase & { type: "cancelled" })
  | (SidecarEventBase & { type: "error"; code: SidecarErrorCode });

export type SidecarErrorCode =
  | "busy"
  | "not_ready"
  | "not_active"
  | "closed"
  | "invalid_request"
  | "startup_error"
  | "provider_error"
  | "unsupported_output"
  | "cancelled"
  | "response_limit"
  | "cleanup_timeout"
  | "cleanup_error"
  | "internal_error";

export interface GenerationIdentity {
  generationId: string;
  epoch: number;
}

export interface GenerationResult {
  requestId: string;
  status: "completed" | "cancelled";
  text: string;
}

export interface GenerationHandle {
  requestId: string;
  generationId: string;
  epoch: number;
  result: Promise<GenerationResult>;
}

export interface ModelSidecarOptions {
  auth?: AuthStorageLike;
  discoverAuth?: () => Promise<AuthStorageLike>;
  model?: SidecarModel;
  loadModel?: () => SidecarModel;
  stream?: StreamSimple;
  onEvent?: (event: SidecarEvent) => void;
  now?: () => number;
  createRequestId?: () => string;
  /**
   * Maximum time allowed for provider iterator/producer cleanup after a
   * generation ends. A timeout poisons this sidecar instance.
   */
  cleanupTimeoutMs?: number;
}

export class SidecarError extends Error {
  readonly code: SidecarErrorCode;

  constructor(code: SidecarErrorCode) {
    super(code);
    this.name = "SidecarError";
    this.code = code;
  }
}

interface ActiveRequest {
  requestId: string;
  epoch: number;
  controller: AbortController;
  promise: Promise<GenerationResult>;
}

interface RequestDiagnostics {
  readonly requestId: string;
  readonly epoch: number;
  readonly startedAt: number;
  readonly marks: LatencyMarks;
}

type ResultBearingStream = AsyncIterable<AssistantMessageEvent> & {
  result?: () => Promise<unknown>;
};

class CleanupTimeoutError extends Error {
  constructor() {
    super("provider cleanup timed out");
    this.name = "CleanupTimeoutError";
  }
}

function defaultModel(): SidecarModel {
  return getBundledModel<"openai-codex-responses">(PROVIDER, MODEL_ID);
}

function validateModel(model: SidecarModel): SidecarModel {
  if (model.provider !== PROVIDER || model.id !== MODEL_ID || model.api !== API) {
    throw new SidecarError("startup_error");
  }
  return model;
}

function defaultStream(
  model: SidecarModel,
  context: Context,
  options?: SimpleStreamOptions,
): AsyncIterable<AssistantMessageEvent> {
  return streamSimple(model, context, options);
}

function emptyUsage(): Usage {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

/**
 * Adapt Lilavel's provider-neutral text context to pi-ai's typed history.
 * Required assistant fields are local adapter metadata; caller-owned context
 * never carries provider payloads, response IDs, usage, or session state.
 */
export function toPiAiContext(messages: readonly ContextMessage[]): Context {
  validateContextMessages(messages);
  const timestamp = Date.now();
  const mapped: Message[] = messages.map((message, index) => {
    const messageTimestamp = timestamp + index;
    if (message.role === "user") {
      return { role: "user", content: message.text, timestamp: messageTimestamp };
    }
    return {
      role: "assistant",
      content: [{ type: "text", text: message.text }],
      api: API,
      provider: PROVIDER,
      model: MODEL_ID,
      usage: emptyUsage(),
      stopReason: "stop",
      timestamp: messageTimestamp,
    };
  });
  return { messages: mapped };
}

function isAbortError(error: unknown): boolean {
  return error instanceof SidecarError && error.code === "cancelled";
}

export class ModelSidecar {
  #state: SidecarState = "new";
  #auth: AuthStorageLike | undefined;
  #model: SidecarModel | undefined;
  #active: ActiveRequest | undefined;
  #startPromise: Promise<void> | undefined;
  #closePromise: Promise<void> | undefined;
  #closed = false;
  readonly #discoverAuth: () => Promise<AuthStorageLike>;
  readonly #loadModel: () => SidecarModel;
  readonly #stream: StreamSimple;
  readonly #onEvent: (event: SidecarEvent) => void;
  readonly #now: () => number;
  readonly #createRequestId: () => string;
  readonly #cleanupTimeoutMs: number;
  #poisoned = false;

  constructor(options: ModelSidecarOptions = {}) {
    this.#auth = options.auth;
    this.#model = options.model ? validateModel(options.model) : undefined;
    this.#discoverAuth = options.discoverAuth ?? (async () => discoverAuthStorage());
    this.#loadModel = options.loadModel ?? defaultModel;
    this.#stream = options.stream ?? defaultStream;
    this.#onEvent = options.onEvent ?? (() => undefined);
    this.#now = options.now ?? (() => performance.now());
    this.#createRequestId = options.createRequestId ?? (() => randomUUID());
    this.#cleanupTimeoutMs = options.cleanupTimeoutMs ?? 5_000;
    if (!Number.isFinite(this.#cleanupTimeoutMs) || this.#cleanupTimeoutMs <= 0) {
      throw new RangeError("cleanupTimeoutMs must be a positive finite number");
    }
  }

  get state(): SidecarState {
    return this.#state;
  }

  get model(): SidecarModel | undefined {
    return this.#model;
  }

  async start(): Promise<void> {
    if (this.#state === "ready" || this.#state === "busy") return;
    if (this.#state === "closed" || this.#closed) throw new SidecarError("closed");
    if (this.#state === "failed" || this.#poisoned) throw new SidecarError("not_ready");
    if (this.#startPromise) return this.#startPromise;

    this.#state = "starting";
    this.#startPromise = this.#startInternal();
    return this.#startPromise;
  }

  generate(input: ModelInput, identity?: GenerationIdentity): GenerationHandle {
    const requestId = identity?.generationId ?? this.#createRequestId();
    const epoch = identity?.epoch ?? 1;
    try {
      validateGenerationId(requestId);
      validateEpoch(epoch);
    } catch {
      return this.#rejectedHandle(requestId, epoch, "invalid_request");
    }
    let request: ModelInput = input;
    try {
      if (typeof input === "string") request = input;
      else if (Array.isArray(input)) {
        validateContextMessages(input);
        request = input;
      } else if (isStructuredInput(input)) {
        validateContextMessages(input.messages);
        if ("system_prompt" in input) validateSystemPrompt(input.system_prompt);
        request = {
          messages: input.messages.map((message) => ({ role: message.role, text: message.text })),
          ...(input.system_prompt === undefined ? {} : { system_prompt: input.system_prompt }),
        };
      }
    } catch {
      return this.#rejectedHandle(requestId, epoch, "invalid_request");
    }
    if (this.#closed || this.#state === "closed") return this.#rejectedHandle(requestId, epoch, "closed");
    if (this.#state !== "ready") return this.#rejectedHandle(requestId, epoch, "not_ready");
    if (this.#active) return this.#rejectedHandle(requestId, epoch, "busy");

    const model = this.#model;
    const auth = this.#auth;
    if (!model || !auth) return this.#rejectedHandle(requestId, epoch, "not_ready");

    const diagnostics: RequestDiagnostics = {
      requestId,
      epoch,
      startedAt: this.#now(),
      marks: { requestReceived: 0 },
    };
    const controller = new AbortController();
    this.#state = "busy";
    let resolveResult!: (result: GenerationResult) => void;
    let rejectResult!: (error: unknown) => void;
    const promise = new Promise<GenerationResult>((resolve, reject) => {
      resolveResult = resolve;
      rejectResult = reject;
    });
    this.#active = { requestId, epoch, controller, promise };
    // Install the active record before notifying observers. This makes a
    // cancel issued from an accepted/request callback deterministic too.
    this.#emit("request_received", diagnostics, {});
    void this.#runRequest(request, model, auth, controller, diagnostics).then(resolveResult, rejectResult);
    const cleanup = () => {
      if (this.#active?.requestId === requestId) this.#active = undefined;
      if (!this.#closed && !this.#poisoned) this.#state = "ready";
    };
    void promise.then(cleanup, cleanup);
    return { requestId, generationId: requestId, epoch, result: promise };
  }

  cancel(requestId: string, epoch?: number): boolean {
    if (this.#active?.requestId !== requestId || (epoch !== undefined && this.#active.epoch !== epoch)) return false;
    this.#active.controller.abort();
    return true;
  }

  async close(): Promise<void> {
    if (this.#closePromise) return this.#closePromise;
    this.#closePromise = this.#closeInternal();
    return this.#closePromise;
  }

  async #closeInternal(): Promise<void> {
    if (this.#state === "closed") return;
    this.#closed = true;
    this.#active?.controller.abort();
    const activePromise = this.#active?.promise;
    if (activePromise) await activePromise.catch(() => undefined);
    if (this.#startPromise) await this.#startPromise.catch(() => undefined);
    this.#auth?.close();
    this.#auth = undefined;
    this.#state = "closed";
  }

  async #startInternal(): Promise<void> {
    try {
      const auth = this.#auth ?? (await this.#discoverAuth());
      if (this.#closed) {
        auth.close();
        throw new SidecarError("closed");
      }
      this.#auth = auth;
      this.#model = validateModel(this.#model ?? this.#loadModel());
      this.#state = "ready";
      this.#emit("ready", undefined, {});
    } catch (error) {
      if (this.#auth && this.#state !== "closed") this.#auth.close();
      this.#auth = undefined;
      if (!this.#closed) {
        this.#state = "failed";
        this.#emit("error", undefined, { code: "startup_error" });
      }
      if (error instanceof SidecarError && error.code === "closed") throw error;
      throw new SidecarError("startup_error");
    }
  }

  async #runRequest(
    input: ModelInput,
    model: SidecarModel,
    auth: AuthStorageLike,
    controller: AbortController,
    diagnostics: RequestDiagnostics,
  ): Promise<GenerationResult> {
    let text = "";
    let iterator: AsyncIterator<AssistantMessageEvent> | undefined;
    let providerResult: (() => Promise<unknown>) | undefined;
    let result: GenerationResult | undefined;
    let failure: SidecarError | undefined;
    try {
      const context: Context =
        typeof input === "string"
          ? { messages: [{ role: "user", content: input, timestamp: Date.now() }] }
          : toPiAiContext(isStructuredInput(input) ? input.messages : input);
      if (isStructuredInput(input) && input.system_prompt !== undefined) {
        context.systemPrompt = [...input.system_prompt];
      }
      const apiKey: ApiKeyResolver = auth.resolver(PROVIDER, {
        sessionId: diagnostics.requestId,
        modelId: model.id,
        baseUrl: model.baseUrl,
      });

      this.#mark(diagnostics, "providerDispatch");
      this.#emit("provider_dispatch", diagnostics, {});
      const stream = this.#stream(model, context, {
        apiKey,
        signal: controller.signal,
        preferWebsockets: false,
      });
      const resultMethod = (stream as ResultBearingStream).result;
      if (typeof resultMethod === "function") providerResult = () => resultMethod.call(stream);
      iterator = stream[Symbol.asyncIterator]();
      let firstEvent = true;
      while (true) {
        const next = iterator.next();
        const item = await this.#nextOrCancelled(next, controller.signal);
        if (item.done) break;
        const received = item.value;
        if (firstEvent) {
          firstEvent = false;
          this.#mark(diagnostics, "providerFirstEvent");
          this.#emit("provider_first_event", diagnostics, { providerEventType: received.type });
        }
        if (received.type === "text_delta") {
          if (received.delta.length > 0) this.#mark(diagnostics, "firstNonEmptyDelta");
          const nextText = text + received.delta;
          if (new TextEncoder().encode(nextText).byteLength > PROTOCOL_LIMITS.maxResponseBytes) {
            // Let the protocol server observe the overflowing delta so it can
            // emit one output_limit terminal frame and cancel provider work.
            this.#emit("text_delta", diagnostics, { delta: received.delta });
            throw new SidecarError("response_limit");
          }
          text = nextText;
          this.#emit("text_delta", diagnostics, { delta: received.delta });
        } else if (received.type === "error") {
          if (received.reason === "aborted" || controller.signal.aborted) throw new SidecarError("cancelled");
          throw new SidecarError("provider_error");
        } else if (received.type === "done" && received.reason === "toolUse") {
          throw new SidecarError("unsupported_output");
        } else if (received.type === "toolcall_start" || received.type === "toolcall_delta" || received.type === "toolcall_end") {
          throw new SidecarError("unsupported_output");
        }
        if (received.type === "done") break;
      }
      if (controller.signal.aborted) throw new SidecarError("cancelled");
      result = { requestId: diagnostics.requestId, status: "completed", text };
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) {
        result = { requestId: diagnostics.requestId, status: "cancelled", text };
      }
      else if (error instanceof SidecarError) failure = error;
      else failure = new SidecarError("provider_error");
    } finally {
      const cleanupFailure = await this.#cleanup(iterator, providerResult);
      if (cleanupFailure) {
        failure = cleanupFailure;
        result = undefined;
        this.#poisoned = true;
        this.#state = "failed";
      }
    }
    if (result?.status === "cancelled") {
      this.#mark(diagnostics, "cancellation");
      this.#emit("cancelled", diagnostics, {});
      return result;
    }
    if (result) {
      this.#mark(diagnostics, "completion");
      this.#emit("completed", diagnostics, { textLength: result.text.length });
      return result;
    }
    const terminalFailure = failure ?? new SidecarError("provider_error");
    if (terminalFailure.code === "response_limit") {
      this.#mark(diagnostics, "cancellation");
      this.#emit("cancelled", diagnostics, {});
    }
    this.#emit("error", diagnostics, { code: terminalFailure.code });
    throw terminalFailure;
  }

  async #cleanup(
    iterator: AsyncIterator<AssistantMessageEvent> | undefined,
    providerResult: (() => Promise<unknown>) | undefined,
  ): Promise<SidecarError | undefined> {
    if (!iterator && !providerResult) return undefined;

    const operations: Promise<unknown>[] = [];
    if (iterator) {
      const iteratorCleanup = Promise.resolve().then(() => iterator.return?.());
      // A timed-out race may leave the provider promise running; observe its
      // eventual rejection so containment never creates an unhandled error.
      void iteratorCleanup.catch(() => undefined);
      operations.push(iteratorCleanup);
    }
    if (providerResult) {
      const resultCleanup = Promise.resolve().then(providerResult);
      void resultCleanup.catch(() => undefined);
      operations.push(resultCleanup);
    }

    const allCleanup = Promise.all(operations);
    void allCleanup.catch(() => undefined);
    try {
      await this.#withTimeout(allCleanup, this.#cleanupTimeoutMs);
      return undefined;
    } catch (error) {
      return new SidecarError(error instanceof CleanupTimeoutError ? "cleanup_timeout" : "cleanup_error");
    }
  }

  async #nextOrCancelled<T>(
    next: Promise<IteratorResult<T>>,
    signal: AbortSignal,
  ): Promise<IteratorResult<T>> {
    if (signal.aborted) {
      // next() has already been called. Observe a late provider rejection even
      // when cancellation wins before we attach the normal race handlers.
      void next.catch(() => undefined);
      throw new SidecarError("cancelled");
    }
    let abortListener: (() => void) | undefined;
    const abort = new Promise<never>((_, reject) => {
      abortListener = () => reject(new SidecarError("cancelled"));
      signal.addEventListener("abort", abortListener, { once: true });
    });
    try {
      return await Promise.race([next, abort]);
    } catch (error) {
      void next.catch(() => undefined);
      throw error;
    } finally {
      if (abortListener) signal.removeEventListener("abort", abortListener);
    }
  }

  async #withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        promise,
        new Promise<T>((_, reject) => {
          timer = setTimeout(() => reject(new CleanupTimeoutError()), timeoutMs);
        }),
      ]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  #rejectedHandle(requestId: string, epoch: number, code: SidecarErrorCode): GenerationHandle {
    const diagnostics: RequestDiagnostics = {
      requestId,
      epoch,
      startedAt: this.#now(),
      marks: { requestReceived: 0 },
    };
    this.#emit("error", diagnostics, { code });
    return { requestId, generationId: requestId, epoch, result: Promise.reject(new SidecarError(code)) };
  }

  #mark(diagnostics: RequestDiagnostics, name: keyof LatencyMarks): void {
    if (diagnostics.marks[name] !== undefined) return;
    diagnostics.marks[name] = Math.max(0, this.#now() - diagnostics.startedAt);
  }

  #emit<T extends SidecarEvent["type"]>(
    type: T,
    diagnostics: RequestDiagnostics | undefined,
    payload: Omit<Extract<SidecarEvent, { type: T }>, keyof SidecarEventBase | "type">,
  ): void {
    const model = this.#model;
    const event = {
      type,
      ...(diagnostics ? { requestId: diagnostics.requestId } : {}),
      ...(diagnostics ? { epoch: diagnostics.epoch } : {}),
      atMs: this.#now(),
      metadata: {
        marks: diagnostics ? { ...diagnostics.marks } : {},
        ...(model ? { modelId: model.id, provider: model.provider, api: model.api } : {}),
      },
      ...payload,
    } as SidecarEvent;
    try {
      this.#onEvent(event);
    } catch {
      // Diagnostic observers cannot change provider lifecycle.
    }
  }
}

export function createModelSidecar(options?: ModelSidecarOptions): ModelSidecar {
  return new ModelSidecar(options);
}
