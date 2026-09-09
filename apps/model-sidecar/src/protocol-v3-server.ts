import { createPiToolProvider, type PiToolProvider } from "./pi-tool-provider.js";
import { encodeV3Event, parseV3Command, type V3Command, type V3Event } from "./protocol-v3.js";
import { readProtocolLines } from "./protocol.js";
import { ProtocolWriter, type ProtocolOutput } from "./server.js";
import { ToolLoopSidecar } from "./tool-loop.js";

const STARTUP_TIMEOUT_MS = 30_000;
const SHUTDOWN_TIMEOUT_MS = 5_000;
const WRITE_TIMEOUT_MS = 5_000;

export interface ProtocolV3ServerOptions {
  readonly input?: ReadableStream<Uint8Array>;
  readonly output?: ProtocolOutput;
  readonly createProvider?: () => Promise<PiToolProvider>;
  readonly startupTimeoutMs?: number;
  readonly shutdownTimeoutMs?: number;
  readonly writeTimeoutMs?: number;
}

/** Explicit V3 host. The ordinary production entrypoint still instantiates V2. */
export class ProtocolV3Server {
  readonly #input: ReadableStream<Uint8Array>;
  readonly #writer: ProtocolWriter;
  readonly #createProvider: () => Promise<PiToolProvider>;
  readonly #startupTimeoutMs: number;
  readonly #shutdownTimeoutMs: number;
  #fatal = false;

  constructor(options: ProtocolV3ServerOptions = {}) {
    this.#input = options.input ?? Bun.stdin.stream();
    this.#createProvider = options.createProvider ?? createPiToolProvider;
    this.#startupTimeoutMs = options.startupTimeoutMs ?? STARTUP_TIMEOUT_MS;
    this.#shutdownTimeoutMs = options.shutdownTimeoutMs ?? SHUTDOWN_TIMEOUT_MS;
    this.#writer = new ProtocolWriter(
      options.output ?? (process.stdout as unknown as ProtocolOutput),
      () => { this.#fatal = true; },
      options.writeTimeoutMs ?? WRITE_TIMEOUT_MS,
      (event) => `${encodeV3Event(event as V3Event)}\n`,
    );
  }

  async run(): Promise<number> {
    let provider: PiToolProvider;
    try {
      provider = await withTimeout(this.#createProvider(), this.#startupTimeoutMs);
    } catch {
      this.#write({ protocol_version: 3, type: "failed", code: "startup_error" });
      await this.#writer.flush().catch(() => undefined);
      return 1;
    }
    const loop = new ToolLoopSidecar({ runProviderTurn: provider.run, emit: (event) => this.#write(event) });
    this.#write({
      protocol_version: 3,
      type: "ready",
      provider: provider.provider,
      model_id: provider.modelId,
      api: provider.api,
    });
    try {
      for await (const line of readProtocolLines(this.#input)) {
        let command: V3Command;
        try { command = parseV3Command(line); }
        catch {
          this.#write({ protocol_version: 3, type: "failed", code: "protocol_error" });
          this.#fatal = true;
          break;
        }
        if (!(await this.#dispatch(loop, command))) break;
        if (this.#fatal) break;
      }
    } catch {
      this.#fatal = true;
    }
    loop.close();
    const settled = await withTimeout(loop.waitIdle(), this.#shutdownTimeoutMs).then(() => true, () => false);
    provider.close();
    if (!settled) this.#fatal = true;
    await this.#writer.flush().catch(() => { this.#fatal = true; });
    return this.#fatal ? 1 : 0;
  }

  async #dispatch(loop: ToolLoopSidecar, command: V3Command): Promise<boolean> {
    if (command.type === "generate") {
      try { loop.start(command); }
      catch (error) {
        this.#write({
          protocol_version: 3,
          type: "failed",
          generation_id: command.generation_id,
          epoch: command.epoch,
          code: error instanceof Error && error.message === "busy" ? "busy" : "protocol_error",
        });
      }
      return true;
    }
    if (command.type === "tool_results") {
      try { loop.submit(command); }
      catch {
        this.#write({
          protocol_version: 3,
          type: "failed",
          generation_id: command.generation_id,
          epoch: command.epoch,
          code: "protocol_error",
        });
        loop.close();
        this.#fatal = true;
      }
      return true;
    }
    if (command.type === "cancel") {
      if (!loop.cancel(command.generation_id, command.epoch)) {
        this.#write({
          protocol_version: 3,
          type: "failed",
          generation_id: command.generation_id,
          epoch: command.epoch,
          code: "not_active",
        });
      }
      return true;
    }
    if (command.type === "health") {
      this.#write({
        protocol_version: 3,
        type: "health",
        state: loop.busy ? "busy" : "ready",
      });
      return true;
    }
    loop.close();
    const settled = await withTimeout(loop.waitIdle(), this.#shutdownTimeoutMs).then(() => true, () => false);
    if (!settled) {
      this.#fatal = true;
      return false;
    }
    this.#write({ protocol_version: 3, type: "shutdown" });
    return false;
  }

  #write(event: V3Event): void {
    if (!this.#writer.write(event)) this.#fatal = true;
  }
}

export async function runProtocolV3Server(options?: ProtocolV3ServerOptions): Promise<number> {
  return new ProtocolV3Server(options).run();
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timer = setTimeout(() => reject(new Error("timeout")), timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
