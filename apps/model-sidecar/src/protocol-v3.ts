import type { FinalToolCall, JsonValue, LilavelToolSpec } from "./tool-transport.js";

export type V3ToolResult = { call_id: string; status: "ok" | "denied" | "invalid" | "unavailable" | "failed" | "timed_out"; reason_code: string | null; output: JsonValue; effect: "none" | "confirmed" | "unknown" };
export type V3Command = { protocol_version: 3; type: "generate"; generation_id: string; epoch: number; prompt: string; tools?: LilavelToolSpec[] } | { protocol_version: 3; type: "tool_results"; generation_id: string; epoch: number; round: number; results: V3ToolResult[] };
export type V3Event = { protocol_version: 3; type: "tool_calls"; generation_id: string; epoch: number; round: number; calls: FinalToolCall[] };

function record(value: unknown): Record<string, unknown> | undefined { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }
function exact(value: Record<string, unknown>, keys: readonly string[]): boolean { return Object.keys(value).length === keys.length && Object.keys(value).every((key) => keys.includes(key)); }
function identity(value: Record<string, unknown>): boolean { return typeof value.generation_id === "string" && value.generation_id.length > 0 && Number.isSafeInteger(value.epoch) && (value.epoch as number) >= 0; }
function validCall(value: unknown): boolean { const call = record(value); return call !== undefined && exact(call, ["local_call_id", "tool_name", "arguments", "argument_error"]) && typeof call.local_call_id === "string" && typeof call.tool_name === "string" && ((record(call.arguments) !== undefined && call.argument_error === null) || (call.arguments === null && (call.argument_error === "invalid_json" || call.argument_error === "invalid_shape"))); }
function validResult(value: unknown): boolean { const result = record(value); return result !== undefined && exact(result, ["call_id", "status", "reason_code", "output", "effect"]) && typeof result.call_id === "string" && ["ok", "denied", "invalid", "unavailable", "failed", "timed_out"].includes(result.status as string) && (result.reason_code === null || typeof result.reason_code === "string") && ["none", "confirmed", "unknown"].includes(result.effect as string); }
function validTool(value: unknown): boolean { const tool = record(value); return tool !== undefined && exact(tool, ["name", "description", "input_schema"]) && typeof tool.name === "string" && typeof tool.description === "string" && record(tool.input_schema) !== undefined; }

export function parseV3Command(line: string): V3Command {
  let value: unknown; try { value = JSON.parse(line); } catch { throw new Error("malformed"); }
  const frame = record(value); if (!frame || frame.protocol_version !== 3 || !identity(frame) || typeof frame.type !== "string") throw new Error("invalid_frame");
  if (frame.type === "generate" && typeof frame.prompt === "string" && exact(frame, ["protocol_version", "type", "generation_id", "epoch", "prompt", ...("tools" in frame ? ["tools"] : [])]) && (!("tools" in frame) || (Array.isArray(frame.tools) && frame.tools.every(validTool)))) return frame as unknown as V3Command;
  if (frame.type === "tool_results" && Number.isSafeInteger(frame.round) && (frame.round as number) >= 1 && Array.isArray(frame.results) && frame.results.every(validResult) && exact(frame, ["protocol_version", "type", "generation_id", "epoch", "round", "results"])) return frame as unknown as V3Command;
  throw new Error("invalid_frame");
}
export function parseV3Event(line: string): V3Event {
  let value: unknown; try { value = JSON.parse(line); } catch { throw new Error("malformed"); }
  const frame = record(value); if (!frame || frame.protocol_version !== 3 || frame.type !== "tool_calls" || !identity(frame) || !Number.isSafeInteger(frame.round) || (frame.round as number) < 1 || !Array.isArray(frame.calls) || !frame.calls.every(validCall) || !exact(frame, ["protocol_version", "type", "generation_id", "epoch", "round", "calls"])) throw new Error("invalid_frame");
  return frame as unknown as V3Event;
}
