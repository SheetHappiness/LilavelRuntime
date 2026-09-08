import type { FinalToolCall } from "./tool-transport.js";

export interface ToolResultFrame {
  readonly generation_id: string;
  readonly epoch: number;
  readonly round: number;
  readonly results: readonly { call_id: string }[];
}

/** V3 transport-only state. It never executes a tool or retains a completed generation. */
export class ToolWaitState {
  #pending: { generationId: string; epoch: number; round: number; callIds: readonly string[] } | undefined;

  open(generationId: string, epoch: number, round: number, calls: readonly FinalToolCall[]): void {
    if (this.#pending || round < 1 || calls.length === 0) throw new Error("illegal_tool_calls");
    const callIds = calls.map((call) => call.local_call_id);
    if (new Set(callIds).size !== callIds.length) throw new Error("duplicate_call_id");
    this.#pending = { generationId, epoch, round, callIds };
  }

  consume(frame: ToolResultFrame): void {
    const pending = this.#pending;
    if (!pending || pending.generationId !== frame.generation_id || pending.epoch !== frame.epoch || pending.round !== frame.round) {
      throw new Error("illegal_tool_results");
    }
    const ids = frame.results.map((result) => result.call_id);
    if (ids.length !== pending.callIds.length || new Set(ids).size !== ids.length || ids.some((id, index) => id !== pending.callIds[index])) {
      throw new Error("result_set_mismatch");
    }
    this.#pending = undefined;
  }

  assertTerminalAllowed(): void {
    if (this.#pending) throw new Error("pending_tool_results");
  }
}
