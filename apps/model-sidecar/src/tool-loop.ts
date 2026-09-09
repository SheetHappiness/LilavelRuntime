import type {
  AssistantMessage,
  AssistantMessageEvent,
  Context,
  RawSseEvent,
  ToolCall as PiToolCall,
  ToolResultMessage,
} from "@oh-my-pi/pi-ai";

import type { V3Command, V3Event, V3ToolResult } from "./protocol-v3.js";
import { ToolWaitState } from "./tool-state.js";
import {
  finalizeOrdinaryFunctionCalls,
  GenerationCallMap,
  mapTools,
  RawFunctionCallCollector,
  TOOL_LIMITS,
  ToolTransportError,
  type FinalToolCall,
} from "./tool-transport.js";

export interface ProviderTurnHooks {
  readonly onEvent: (event: AssistantMessageEvent) => void;
  readonly onSseEvent: (event: RawSseEvent) => void;
}

/** Resolves only after provider iterator/producer cleanup is confirmed. */
export type ProviderTurn = (
  context: Context,
  signal: AbortSignal,
  hooks: ProviderTurnHooks,
) => Promise<AssistantMessage>;

export interface ToolLoopSidecarOptions {
  readonly runProviderTurn: ProviderTurn;
  readonly emit: (event: V3Event) => void;
}

interface ActiveGeneration {
  readonly generationId: string;
  readonly epoch: number;
  readonly context: Context;
  readonly aliases: ReadonlyMap<string, string>;
  readonly callMap: GenerationCallMap;
  readonly providerNameByLocal: Map<string, string>;
  readonly wait: ToolWaitState;
  readonly controller: AbortController;
  round: number;
  totalCalls: number;
  turnRunning: boolean;
  terminal: boolean;
}

/** Transport-only V3 loop. It never authorizes or executes an application tool. */
export class ToolLoopSidecar {
  readonly #runProviderTurn: ProviderTurn;
  readonly #emit: (event: V3Event) => void;
  #active: ActiveGeneration | undefined;
  #closing = false;
  #idle: Promise<void> = Promise.resolve();
  #resolveIdle: (() => void) | undefined;

  constructor(options: ToolLoopSidecarOptions) {
    this.#runProviderTurn = options.runProviderTurn;
    this.#emit = options.emit;
  }

  get busy(): boolean {
    return this.#active !== undefined;
  }

  waitIdle(): Promise<void> {
    return this.#idle;
  }

  start(command: Extract<V3Command, { type: "generate" }>): void {
    if (this.#closing) throw new Error("closed");
    if (this.#active) throw new Error("busy");
    this.#idle = new Promise<void>((resolve) => {
      this.#resolveIdle = resolve;
    });
    const mapped = mapTools(command.tools ?? []);
    const context = commandToContext(command);
    context.tools = [...mapped.tools];
    const active: ActiveGeneration = {
      generationId: command.generation_id,
      epoch: command.epoch,
      context,
      aliases: mapped.applicationByProviderAlias,
      callMap: new GenerationCallMap(),
      providerNameByLocal: new Map(),
      wait: new ToolWaitState(),
      controller: new AbortController(),
      round: 0,
      totalCalls: 0,
      turnRunning: false,
      terminal: false,
    };
    this.#active = active;
    this.#emit(identityEvent("accepted", active));
    void this.#runTurn(active);
  }

  submit(command: Extract<V3Command, { type: "tool_results" }>): void {
    const active = this.#require(command.generation_id, command.epoch);
    if (active.turnRunning || active.terminal) throw new Error("illegal_tool_results");
    active.wait.consume(command);
    const messages = command.results.map((result) => this.#toolResult(active, result));
    active.context.messages.push(...messages);
    this.#assertReplayBound(active);
    void this.#runTurn(active);
  }

  cancel(generationId: string, epoch: number): boolean {
    const active = this.#active;
    if (!active || active.generationId !== generationId || active.epoch !== epoch || active.terminal) {
      return false;
    }
    active.wait.cancel();
    active.controller.abort();
    if (!active.turnRunning) this.#finish(active, "cancelled");
    return true;
  }

  close(): void {
    this.#closing = true;
    const active = this.#active;
    if (active) {
      active.wait.cancel();
      active.controller.abort();
      if (!active.turnRunning) this.#finish(active, "cancelled");
    }
  }

  async #runTurn(active: ActiveGeneration): Promise<void> {
    if (this.#active !== active || active.terminal || active.turnRunning) return;
    active.turnRunning = true;
    const collector = new RawFunctionCallCollector();
    const calls: PiToolCall[] = [];
    let doneReason: "stop" | "length" | "toolUse" | undefined;
    try {
      const message = await this.#runProviderTurn(active.context, active.controller.signal, {
        onSseEvent: (event) => collector.observe(event),
        onEvent: (event) => {
          if (event.type === "text_delta") {
            for (const delta of splitText(event.delta)) {
              this.#emit({
                protocol_version: 3,
                type: "text_delta",
                generation_id: active.generationId,
                epoch: active.epoch,
                delta,
              });
            }
          } else if (event.type === "toolcall_end") {
            calls.push(event.toolCall);
          } else if (event.type === "done") {
            doneReason = event.reason;
          } else if (event.type === "error") {
            throw new Error(event.reason === "aborted" ? "cancelled" : "provider_error");
          }
        },
      });
      active.turnRunning = false;
      if (active.controller.signal.aborted) {
        this.#finish(active, "cancelled");
        return;
      }
      active.context.messages.push(message);
      this.#assertReplayBound(active);
      if (doneReason === "toolUse") {
        this.#finalizeCalls(active, calls, collector);
        return;
      }
      if (calls.length !== 0 || collector.pendingCount !== 0) throw new Error("correspondence");
      active.wait.assertTerminalAllowed();
      this.#finish(active, "completed");
    } catch (error) {
      active.turnRunning = false;
      const failureCode = error instanceof Error ? error.message : "provider_error";
      if (failureCode === "cleanup_timeout" || failureCode === "cleanup_error") {
        this.#fail(active, failureCode);
      } else if (active.controller.signal.aborted || failureCode === "cancelled") {
        this.#finish(active, "cancelled");
      } else {
        const code = error instanceof ToolTransportError ? "protocol_error" : "provider_error";
        this.#fail(active, code);
      }
    }
  }

  #finalizeCalls(
    active: ActiveGeneration,
    calls: readonly PiToolCall[],
    collector: RawFunctionCallCollector,
  ): void {
    const nextRound = active.round + 1;
    if (nextRound > TOOL_LIMITS.maxRoundsPerGeneration) throw new ToolTransportError("bounds");
    const finalized = finalizeOrdinaryFunctionCalls(
      calls,
      collector,
      active.callMap,
      active.aliases,
    );
    if (collector.pendingCount !== 0) throw new ToolTransportError("correspondence");
    if (active.totalCalls + finalized.length > TOOL_LIMITS.maxCallsPerGeneration) {
      throw new ToolTransportError("bounds");
    }
    active.round = nextRound;
    active.totalCalls += finalized.length;
    for (let index = 0; index < finalized.length; index += 1) {
      const local = finalized[index];
      const provider = calls[index];
      if (!local || !provider) throw new ToolTransportError("correspondence");
      active.providerNameByLocal.set(local.local_call_id, provider.name);
    }
    active.wait.open(active.generationId, active.epoch, active.round, finalized);
    this.#emit({
      protocol_version: 3,
      type: "tool_calls",
      generation_id: active.generationId,
      epoch: active.epoch,
      round: active.round,
      calls: finalized,
    });
  }

  #toolResult(active: ActiveGeneration, result: V3ToolResult): ToolResultMessage {
    const providerId = active.callMap.providerFor(result.call_id);
    const providerName = active.providerNameByLocal.get(result.call_id);
    if (!providerId || !providerName) throw new Error("result_set_mismatch");
    const output = JSON.stringify({
      status: result.status,
      reason_code: result.reason_code,
      output: result.output,
      effect: result.effect,
    });
    if (new TextEncoder().encode(output).byteLength > TOOL_LIMITS.maxResultBytes) {
      throw new ToolTransportError("bounds");
    }
    return {
      role: "toolResult",
      toolCallId: providerId,
      toolName: providerName,
      content: [{ type: "text", text: output }],
      isError: result.status !== "ok",
      timestamp: Date.now(),
    };
  }

  #assertReplayBound(active: ActiveGeneration): void {
    const bytes = new TextEncoder().encode(JSON.stringify(active.context.messages)).byteLength;
    if (bytes > TOOL_LIMITS.maxTransientContextBytes) throw new ToolTransportError("bounds");
  }

  #require(generationId: string, epoch: number): ActiveGeneration {
    const active = this.#active;
    if (!active || active.generationId !== generationId || active.epoch !== epoch) {
      throw new Error("not_active");
    }
    return active;
  }

  #finish(active: ActiveGeneration, type: "completed" | "cancelled"): void {
    if (this.#active !== active || active.terminal) return;
    active.wait.assertTerminalAllowed();
    active.terminal = true;
    this.#emit(identityEvent(type, active));
    this.#active = undefined;
    this.#resolveIdle?.();
    this.#resolveIdle = undefined;
  }

  #fail(
    active: ActiveGeneration,
    code: "provider_error" | "protocol_error" | "cleanup_timeout" | "cleanup_error",
  ): void {
    if (this.#active !== active || active.terminal) return;
    active.terminal = true;
    this.#emit({ ...identityEvent("failed", active), code });
    this.#active = undefined;
    if (code === "cleanup_timeout" || code === "cleanup_error") this.#closing = true;
    this.#resolveIdle?.();
    this.#resolveIdle = undefined;
  }
}

function commandToContext(command: Extract<V3Command, { type: "generate" }>): Context {
  const timestamp = Date.now();
  if (command.prompt !== undefined) {
    return {
      messages: [{ role: "user", content: command.prompt, timestamp }],
      ...(command.system_prompt ? { systemPrompt: [...command.system_prompt] } : {}),
    };
  }
  return {
    messages: command.messages.map((message, index) =>
      message.role === "user"
        ? { role: "user" as const, content: message.text, timestamp: timestamp + index }
        : assistantContextMessage(message.text, timestamp + index),
    ),
    ...(command.system_prompt ? { systemPrompt: [...command.system_prompt] } : {}),
  };
}

function assistantContextMessage(text: string, timestamp: number): AssistantMessage {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    api: "openai-codex-responses",
    provider: "openai-codex",
    model: "gpt-5.6-luna",
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
    stopReason: "stop",
    timestamp,
  };
}

function identityEvent<T extends "accepted" | "completed" | "cancelled" | "failed">(
  type: T,
  active: ActiveGeneration,
): Extract<V3Event, { type: T }> {
  return {
    protocol_version: 3,
    type,
    generation_id: active.generationId,
    epoch: active.epoch,
  } as Extract<V3Event, { type: T }>;
}

function splitText(value: string): string[] {
  const chunks: string[] = [];
  let current = "";
  for (const character of value) {
    if (new TextEncoder().encode(current + character).byteLength > 16 * 1024) {
      chunks.push(current);
      current = character;
    } else {
      current += character;
    }
  }
  if (current || chunks.length === 0) chunks.push(current);
  return chunks;
}
