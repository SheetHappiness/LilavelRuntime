import { createModelSidecar, type GenerationHandle, type ModelSidecar, type SidecarEvent } from "./index.js";
import {
  encodeEvent,
  parseCommand,
  PROTOCOL_VERSION,
  PROTOCOL_LIMITS,
  readProtocolLines,
  type ProtocolCommand,
  type ProtocolErrorCode,
  type ProtocolEvent,
} from "./protocol.js";

const DEFAULT_STARTUP_TIMEOUT_MS = 30_000;
const DEFAULT_SHUTDOWN_TIMEOUT_MS = 5_000;
const DEFAULT_TERMINAL_CLEANUP_TIMEOUT_MS = 5_000;
const DEFAULT_WRITE_TIMEOUT_MS = 5_000;
const MAX_PENDING_FRAMES = 64;
const MAX_PENDING_BYTES = PROTOCOL_LIMITS.maxFrameBytes * 2;

export interface ProtocolOutput {
  write(chunk: string): boolean;
  once(event: "drain", listener: () => void): unknown;
}

export type SidecarFactory = (onEvent: (event: SidecarEvent) => void) => ModelSidecar;

export interface ProtocolServerOptions {
  input?: ReadableStream<Uint8Array>;
  output?: ProtocolOutput;
  createSidecar?: SidecarFactory;
  startupTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  terminalCleanupTimeoutMs?: number;
  writeTimeoutMs?: number;
  forceExit?: (code: number) => void;
}

export class ProtocolWriter {
  readonly #output: ProtocolOutput;
  readonly #onFailure: (error: Error) => void;
  readonly #writeTimeoutMs: number;
  readonly #encode: (event: unknown) => string;
  readonly #queue: string[] = [];
  #pendingBytes = 0;
  #drainPromise: Promise<void> | undefined;
  #failure: Error | undefined;
  #closed = false;

  constructor(
    output: ProtocolOutput,
    onFailure: (error: Error) => void,
    writeTimeoutMs: number,
    encode: (event: unknown) => string = (event) => encodeEvent(event as ProtocolEvent),
  ) {
    this.#output = output;
    this.#onFailure = onFailure;
    this.#writeTimeoutMs = writeTimeoutMs;
    this.#encode = encode;
  }

  write(event: unknown): boolean {
    if (this.#closed || this.#failure) return false;
    let line: string;
    try {
      line = this.#encode(event);
    } catch (error) {
      this.#fail(error instanceof Error ? error : new Error("protocol encoding failed"));
      return false;
    }
    const bytes = new TextEncoder().encode(line).length;
    if (this.#queue.length >= MAX_PENDING_FRAMES || this.#pendingBytes + bytes > MAX_PENDING_BYTES) {
      this.#fail(new Error("protocol output buffer limit exceeded"));
      return false;
    }
    this.#queue.push(line);
    this.#pendingBytes += bytes;
    this.#startDrain();
    return true;
  }

  async flush(): Promise<void> {
    while (this.#drainPromise) await this.#drainPromise;
    if (this.#failure) throw this.#failure;
  }

  async close(): Promise<void> {
    this.#closed = true;
    await this.flush();
  }

  #startDrain(): void {
    if (this.#drainPromise) return;
    const pending = this.#drain();
    this.#drainPromise = pending;
    void pending
      .catch(() => undefined)
      .finally(() => {
        if (this.#drainPromise === pending) this.#drainPromise = undefined;
        // More frames can arrive after a synchronous drain finished but
        // before its promise is cleared. Keep that backlog moving too.
        if (this.#queue.length > 0 && !this.#failure) this.#startDrain();
      });
  }

  async #drain(): Promise<void> {
    while (this.#queue.length > 0) {
      const line = this.#queue.shift();
      if (line === undefined) return;
      this.#pendingBytes -= new TextEncoder().encode(line).length;
      let accepted: boolean;
      try {
        accepted = this.#output.write(line);
      } catch (error) {
        this.#fail(error instanceof Error ? error : new Error("protocol output failed"));
        throw this.#failure;
      }
      if (!accepted) {
        try {
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error("protocol output write timed out")), this.#writeTimeoutMs);
            try {
              this.#output.once("drain", () => {
                clearTimeout(timer);
                resolve();
              });
            } catch (error) {
              clearTimeout(timer);
              reject(error);
            }
          });
        } catch (error) {
          this.#fail(error instanceof Error ? error : new Error("protocol output failed"));
          throw this.#failure;
        }
      }
    }
  }

  #fail(error: Error): void {
    if (this.#failure) return;
    this.#failure = error;
    this.#queue.length = 0;
    this.#pendingBytes = 0;
    this.#onFailure(error);
  }
}

interface ActiveGeneration {
  readonly generationId: string;
  readonly epoch: number;
  readonly finished: Promise<void>;
  readonly resolveFinished: () => void;
  handle: GenerationHandle | undefined;
  cancelRequested: boolean;
  outputLimitExceeded: boolean;
  terminal: boolean;
  responseBytes: number;
}

export class ProtocolServer {
  readonly #input: ReadableStream<Uint8Array>;
  readonly #inputAbortController = new AbortController();
  readonly #writer: ProtocolWriter;
  readonly #sidecar: ModelSidecar;
  readonly #startupTimeoutMs: number;
  readonly #shutdownTimeoutMs: number;
  readonly #terminalCleanupTimeoutMs: number;
  readonly #forceExit: (code: number) => void;
  #active: ActiveGeneration | undefined;
  #closing = false;
  #fatal = false;
  #readySeen = false;
  #startupFailureSeen = false;

  constructor(options: ProtocolServerOptions = {}) {
    this.#input = options.input ?? Bun.stdin.stream();
    this.#startupTimeoutMs = options.startupTimeoutMs ?? DEFAULT_STARTUP_TIMEOUT_MS;
    this.#shutdownTimeoutMs = options.shutdownTimeoutMs ?? DEFAULT_SHUTDOWN_TIMEOUT_MS;
    this.#terminalCleanupTimeoutMs = options.terminalCleanupTimeoutMs ?? DEFAULT_TERMINAL_CLEANUP_TIMEOUT_MS;
    const writeTimeoutMs = options.writeTimeoutMs ?? DEFAULT_WRITE_TIMEOUT_MS;
    if (!Number.isFinite(writeTimeoutMs) || writeTimeoutMs <= 0) {
      throw new RangeError("writeTimeoutMs must be a positive finite number");
    }
    this.#forceExit = options.forceExit ?? ((code) => process.exit(code));
    this.#writer = new ProtocolWriter(
      options.output ?? (process.stdout as unknown as ProtocolOutput),
      () => {
        this.#markFatal();
        if (this.#active?.handle) this.#sidecar.cancel(this.#active.generationId, this.#active.epoch);
      },
      writeTimeoutMs,
    );
    const onEvent = (event: SidecarEvent): void => this.#handleSidecarEvent(event);
    this.#sidecar = options.createSidecar?.(onEvent) ?? createModelSidecar({ onEvent });
  }

  async run(): Promise<number> {
    try {
      await this.#withTimeout(this.#sidecar.start(), this.#startupTimeoutMs);
      await this.#writer.flush();
      if (!this.#readySeen) throw new Error("sidecar did not emit ready");
    } catch {
      if (!this.#startupFailureSeen) this.#writeFailed(undefined, undefined, "startup_error");
      await this.#finishAfterFailure();
      return 1;
    }

    let exitCode = 0;
    try {
      for await (const line of readProtocolLines(this.#input, this.#inputAbortController.signal)) {
        if (this.#fatal) {
          exitCode = 1;
          break;
        }
        let command: ProtocolCommand;
        try {
          command = parseCommand(line);
        } catch {
          this.#writeFailed(undefined, undefined, "protocol_error");
          exitCode = 1;
          break;
        }
        const keepRunning = await this.#dispatch(command);
        if (!keepRunning) break;
      }
    } catch {
      this.#writeFailed(undefined, undefined, "protocol_error");
      exitCode = 1;
    }

    if (!this.#closing) {
      const closed = await this.#closeSidecar();
      if (!closed) exitCode = 1;
    }
    await this.#writer.flush().catch(() => {
      exitCode = 1;
    });
    return this.#fatal ? 1 : exitCode;
  }

  async #dispatch(command: ProtocolCommand): Promise<boolean> {
    switch (command.type) {
      case "generate":
        await this.#generate(command);
        return true;
      case "cancel":
        this.#cancel(command.generation_id, command.epoch);
        return true;
      case "health":
        this.#writeHealth();
        return true;
      case "shutdown":
        this.#closing = true;
        if (!(await this.#closeSidecar()) || this.#fatal) return false;
        this.#writeEvent({ protocol_version: PROTOCOL_VERSION, type: "shutdown" });
        await this.#writer.flush().catch(() => undefined);
        return false;
    }
  }

  async #generate(command: Extract<ProtocolCommand, { type: "generate" }>): Promise<void> {
    const active = this.#active;
    if (active?.terminal) {
      const finished = await this.#withTimeout(active.finished, this.#terminalCleanupTimeoutMs).then(
        () => true,
        () => false,
      );
      if (!finished) {
        this.#writeFailed(command.generation_id, command.epoch, "provider_error");
        return;
      }
    }
    if (this.#active) {
      this.#writeFailed(command.generation_id, command.epoch, "busy");
      return;
    }

    let resolveFinished!: () => void;
    const finished = new Promise<void>((resolve) => {
      resolveFinished = resolve;
    });
    const pending: ActiveGeneration = {
      generationId: command.generation_id,
      epoch: command.epoch,
      finished,
      resolveFinished,
      handle: undefined,
      cancelRequested: false,
      outputLimitExceeded: false,
      terminal: false,
      responseBytes: 0,
    };
    this.#active = pending;
    try {
      const input = "prompt" in command
        ? command.system_prompt?.length
          ? { messages: [{ role: "user" as const, text: command.prompt }], system_prompt: command.system_prompt }
          : command.prompt
        : command.system_prompt?.length
          ? { messages: command.messages, system_prompt: command.system_prompt }
          : { messages: command.messages };
      const handle = this.#sidecar.generate(input, {
        generationId: command.generation_id,
        epoch: command.epoch,
      });
      pending.handle = handle;
      void this.#observeGeneration(pending, handle);
    } catch {
      pending.terminal = true;
      this.#writeFailed(command.generation_id, command.epoch, "provider_error");
      this.#finishActive(pending);
    }
  }

  #cancel(generationId: string, epoch: number): void {
    const active = this.#active;
    if (!active || active.generationId !== generationId || active.epoch !== epoch || active.terminal) {
      this.#writeFailed(generationId, epoch, "not_active");
      return;
    }
    if (active.cancelRequested) return;
    active.cancelRequested = true;
    if (!this.#sidecar.cancel(generationId, epoch)) this.#writeFailed(generationId, epoch, "not_active");
  }

  async #observeGeneration(active: ActiveGeneration, handle: GenerationHandle): Promise<void> {
    try {
      const result = await handle.result;
      if (!active.terminal) {
        active.terminal = true;
        this.#writeEvent(
          result.status === "completed"
            ? { protocol_version: PROTOCOL_VERSION, type: "completed", generation_id: active.generationId, epoch: active.epoch }
            : { protocol_version: PROTOCOL_VERSION, type: "cancelled", generation_id: active.generationId, epoch: active.epoch },
        );
      }
    } catch {
      if (!active.terminal) {
        active.terminal = true;
        this.#writeFailed(active.generationId, active.epoch, "provider_error");
      }
    } finally {
      this.#finishActive(active);
    }
  }

  #handleSidecarEvent(event: SidecarEvent): void {
    switch (event.type) {
      case "ready":
        this.#readySeen = true;
        this.#writeEvent({
          protocol_version: PROTOCOL_VERSION,
          type: "ready",
          provider: event.metadata.provider ?? "openai-codex",
          model_id: event.metadata.modelId ?? "gpt-5.6-luna",
          api: event.metadata.api ?? "openai-codex-responses",
        });
        return;
      case "request_received": {
        const active = this.#active;
        if (active && this.#matches(active, event) && !active.terminal) {
          this.#writeEvent({
            protocol_version: PROTOCOL_VERSION,
            type: "accepted",
            generation_id: active.generationId,
            epoch: active.epoch,
          });
        }
        return;
      }
      case "text_delta": {
        const active = this.#active;
        if (!active || !this.#matches(active, event) || active.cancelRequested || active.terminal) return;
        const deltaBytes = new TextEncoder().encode(event.delta).length;
        if (active.responseBytes + deltaBytes > PROTOCOL_LIMITS.maxResponseBytes) {
          active.cancelRequested = true;
          // Keep the generation active until the sidecar confirms terminal
          // cleanup. This prevents a ready/reuse window while provider work
          // is still unwinding.
          active.outputLimitExceeded = true;
          this.#sidecar.cancel(active.generationId, active.epoch);
          return;
        }
        active.responseBytes += deltaBytes;
        for (const delta of splitDelta(event.delta)) {
          this.#writeEvent({
            protocol_version: PROTOCOL_VERSION,
            type: "text_delta",
            generation_id: active.generationId,
            epoch: active.epoch,
            delta,
          });
        }
        return;
      }
      case "completed": {
        const active = this.#active;
        if (!active || !this.#matches(active, event) || active.terminal) return;
        active.terminal = true;
        if (active.outputLimitExceeded) this.#writeFailed(active.generationId, active.epoch, "output_limit");
        else {
          this.#writeEvent({
            protocol_version: PROTOCOL_VERSION,
            type: "completed",
            generation_id: active.generationId,
            epoch: active.epoch,
          });
        }
        return;
      }
      case "cancelled": {
        const active = this.#active;
        if (!active || !this.#matches(active, event) || active.terminal) return;
        active.terminal = true;
        if (active.outputLimitExceeded) this.#writeFailed(active.generationId, active.epoch, "output_limit");
        else {
          this.#writeEvent({
            protocol_version: PROTOCOL_VERSION,
            type: "cancelled",
            generation_id: active.generationId,
            epoch: active.epoch,
          });
        }
        return;
      }
      case "error":
        this.#handleSidecarError(event);
        return;
      case "provider_dispatch":
      case "provider_first_event":
        return;
    }
  }

  #handleSidecarError(event: Extract<SidecarEvent, { type: "error" }>): void {
    const code = toProtocolErrorCode(event.code);
    const poisoned = code === "cleanup_timeout" || code === "cleanup_error";
    if (!event.requestId) {
      this.#startupFailureSeen = code === "startup_error";
      this.#writeFailed(undefined, undefined, code);
      if (poisoned) this.#markFatal();
      return;
    }
    const active = this.#active;
    // Cleanup failures poison the whole sidecar, even if a prior terminal
    // event (for example output_limit) already settled this generation.
    // Never emit a second generation terminal, but do stop accepting work.
    if (poisoned) this.#markFatal();
    if (!active || !this.#matches(active, event) || active.terminal) return;
    active.terminal = true;
    this.#writeFailed(
      active.generationId,
      active.epoch,
      active.outputLimitExceeded && code === "cancelled" ? "output_limit" : code,
    );
  }

  #writeHealth(): void {
    const state = this.#sidecar.state === "new" ? "starting" : this.#sidecar.state;
    this.#writeEvent({ protocol_version: PROTOCOL_VERSION, type: "health", state });
  }

  #writeFailed(generationId: string | undefined, epoch: number | undefined, code: ProtocolErrorCode): void {
    if (generationId !== undefined && epoch !== undefined) {
      this.#writeEvent({ protocol_version: PROTOCOL_VERSION, type: "failed", generation_id: generationId, epoch, code });
    } else {
      this.#writeEvent({ protocol_version: PROTOCOL_VERSION, type: "failed", code });
    }
  }

  #writeEvent(event: ProtocolEvent): void {
    this.#writer.write(event);
  }

  #matches(active: ActiveGeneration, event: SidecarEvent): boolean {
    return event.requestId === active.generationId && (event.epoch === undefined || event.epoch === active.epoch);
  }

  #finishActive(active: ActiveGeneration): void {
    if (this.#active !== active) return;
    this.#active = undefined;
    active.resolveFinished();
  }

  #markFatal(): void {
    if (this.#fatal) return;
    this.#fatal = true;
    this.#inputAbortController.abort();
  }

  async #closeSidecar(): Promise<boolean> {
    try {
      await this.#withTimeout(this.#sidecar.close(), this.#shutdownTimeoutMs);
      return true;
    } catch {
      const active = this.#active;
      if (active && !active.terminal) {
        active.terminal = true;
        this.#writeFailed(active.generationId, active.epoch, "shutdown_timeout");
        this.#finishActive(active);
      }
      this.#markFatal();
      try {
        this.#forceExit(1);
      } catch {
        // A test or embedding host may provide a throwing exit hook. The
        // protocol still reports the forced shutdown as failed below.
      }
      return false;
    }
  }

  async #finishAfterFailure(): Promise<void> {
    await this.#closeSidecar();
    await this.#writer.flush().catch(() => undefined);
  }

  async #withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        promise,
        new Promise<T>((_, reject) => {
          timer = setTimeout(() => reject(new Error("protocol operation timed out")), timeoutMs);
        }),
      ]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }
}

function splitDelta(delta: string): string[] {
  const chunks: string[] = [];
  let current = "";
  let currentBytes = 0;
  const encoder = new TextEncoder();
  for (const character of delta) {
    const characterBytes = encoder.encode(character).length;
    if (current.length > 0 && currentBytes + characterBytes > PROTOCOL_LIMITS.maxDeltaBytes) {
      chunks.push(current);
      current = character;
      currentBytes = characterBytes;
    } else {
      current += character;
      currentBytes += characterBytes;
    }
  }
  if (current.length > 0) chunks.push(current);
  return chunks;
}

function toProtocolErrorCode(code: string): ProtocolErrorCode {
  if (
    code === "busy" ||
    code === "not_ready" ||
    code === "closed" ||
    code === "not_active" ||
    code === "startup_error" ||
    code === "provider_error" ||
    code === "unsupported_output" ||
    code === "protocol_error" ||
    code === "output_limit" ||
    code === "cleanup_timeout" ||
    code === "cleanup_error" ||
    code === "shutdown_timeout" ||
    code === "cancellation_timeout" ||
    code === "cancelled"
  ) {
    return code;
  }
  if (code === "response_limit") return "output_limit";
  return "provider_error";
}

export async function runProtocolServer(options?: ProtocolServerOptions): Promise<number> {
  return new ProtocolServer(options).run();
}
