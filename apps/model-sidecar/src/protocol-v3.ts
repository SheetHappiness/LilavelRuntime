import { PROTOCOL_LIMITS, validateContextMessages, validateSystemPrompt, type ContextMessage, type ProtocolErrorCode } from "./protocol.js";
import { TOOL_LIMITS, type FinalToolCall, type JsonValue, type LilavelToolSpec } from "./tool-transport.js";

export type V3ToolResult = {
  call_id: string;
  status: "ok" | "denied" | "invalid" | "unavailable" | "failed" | "timed_out";
  reason_code: string | null;
  output: JsonValue;
  effect: "none" | "confirmed" | "unknown";
};

type GenerateBase = {
  protocol_version: 3;
  type: "generate";
  generation_id: string;
  epoch: number;
  system_prompt?: string[];
  tools?: LilavelToolSpec[];
};

export type V3Command =
  | (GenerateBase & { prompt: string; messages?: never })
  | (GenerateBase & { messages: ContextMessage[]; prompt?: never })
  | { protocol_version: 3; type: "tool_results"; generation_id: string; epoch: number; round: number; results: V3ToolResult[] }
  | { protocol_version: 3; type: "cancel"; generation_id: string; epoch: number }
  | { protocol_version: 3; type: "health" }
  | { protocol_version: 3; type: "shutdown" };

type Identity = { protocol_version: 3; generation_id: string; epoch: number };
export type V3Event =
  | { protocol_version: 3; type: "ready"; provider: string; model_id: string; api: string }
  | { protocol_version: 3; type: "health"; state: "starting" | "ready" | "busy" | "failed" | "closed" }
  | (Identity & { type: "accepted" | "completed" | "cancelled" })
  | (Identity & { type: "text_delta"; delta: string })
  | (Identity & { type: "tool_calls"; round: number; calls: FinalToolCall[] })
  | (Identity & { type: "failed"; code: ProtocolErrorCode })
  | { protocol_version: 3; type: "failed"; code: ProtocolErrorCode }
  | { protocol_version: 3; type: "shutdown" };

const encoder = new TextEncoder();
const errorCodes = new Set<ProtocolErrorCode>([
  "busy", "not_ready", "closed", "not_active", "startup_error", "provider_error",
  "unsupported_output", "protocol_error", "output_limit", "cleanup_timeout", "cleanup_error",
  "shutdown_timeout", "cancellation_timeout", "cancelled",
]);

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function exact(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function validId(value: unknown): value is string {
  return typeof value === "string"
    && value.length > 0
    && encoder.encode(value).byteLength <= PROTOCOL_LIMITS.maxIdBytes
    && [...value].every((character) => {
      const code = character.codePointAt(0) ?? 0;
      return code >= 0x20 && code !== 0x7f;
    });
}

function validEpoch(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function identity(value: Record<string, unknown>): boolean {
  return validId(value.generation_id) && validEpoch(value.epoch);
}

function validJson(value: unknown): value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(validJson);
  const object = record(value);
  return object !== undefined && Object.entries(object).every(([key, item]) => key.length > 0 && validJson(item));
}

function validCall(value: unknown): value is FinalToolCall {
  const call = record(value);
  return call !== undefined
    && exact(call, ["local_call_id", "tool_name", "arguments", "argument_error"])
    && validId(call.local_call_id)
    && validId(call.tool_name)
    && ((record(call.arguments) !== undefined && validJson(call.arguments) && call.argument_error === null)
      || (call.arguments === null && (call.argument_error === "invalid_json" || call.argument_error === "invalid_shape")));
}

function validResult(value: unknown): value is V3ToolResult {
  const result = record(value);
  if (!result || !exact(result, ["call_id", "status", "reason_code", "output", "effect"])) return false;
  if (!validId(result.call_id)
    || !["ok", "denied", "invalid", "unavailable", "failed", "timed_out"].includes(result.status as string)
    || !(result.reason_code === null || validId(result.reason_code))
    || !validJson(result.output)
    || !["none", "confirmed", "unknown"].includes(result.effect as string)) return false;
  return encoder.encode(JSON.stringify(result.output)).byteLength <= TOOL_LIMITS.maxResultBytes;
}

function validTool(value: unknown): value is LilavelToolSpec {
  const tool = record(value);
  return tool !== undefined
    && exact(tool, ["name", "description", "input_schema"])
    && validId(tool.name)
    && typeof tool.description === "string"
    && encoder.encode(tool.description).byteLength <= 4 * 1024
    && record(tool.input_schema) !== undefined
    && validJson(tool.input_schema);
}

export function parseV3Command(line: string): V3Command {
  const frame = parseFrame(line);
  if (frame.protocol_version !== 3 || typeof frame.type !== "string") throw new Error("invalid_frame");
  if (frame.type === "generate") {
    if (!identity(frame)) throw new Error("invalid_frame");
    const optional = ["system_prompt", "tools"].filter((key) => key in frame);
    const inputKey = "prompt" in frame ? "prompt" : "messages" in frame ? "messages" : undefined;
    if (!inputKey || !exact(frame, ["protocol_version", "type", "generation_id", "epoch", inputKey, ...optional])) throw new Error("invalid_frame");
    try {
      if (inputKey === "prompt") {
        if (typeof frame.prompt !== "string" || encoder.encode(frame.prompt).byteLength > PROTOCOL_LIMITS.maxPromptBytes) throw new Error();
      } else validateContextMessages(frame.messages as ContextMessage[]);
      if ("system_prompt" in frame) validateSystemPrompt(frame.system_prompt);
    } catch { throw new Error("invalid_frame"); }
    if ("tools" in frame && (!Array.isArray(frame.tools)
      || frame.tools.length > TOOL_LIMITS.maxExposedTools
      || !frame.tools.every(validTool)
      || encoder.encode(JSON.stringify(frame.tools)).byteLength > TOOL_LIMITS.maxDefinitionBytes)) throw new Error("invalid_frame");
    return frame as unknown as V3Command;
  }
  if (frame.type === "tool_results") {
    if (!identity(frame)
      || !exact(frame, ["protocol_version", "type", "generation_id", "epoch", "round", "results"])
      || !Number.isSafeInteger(frame.round) || (frame.round as number) < 1
      || !Array.isArray(frame.results) || frame.results.length === 0
      || frame.results.length > TOOL_LIMITS.maxCallsPerBatch || !frame.results.every(validResult)) throw new Error("invalid_frame");
    return frame as unknown as V3Command;
  }
  if (frame.type === "cancel") {
    if (!identity(frame) || !exact(frame, ["protocol_version", "type", "generation_id", "epoch"])) throw new Error("invalid_frame");
    return frame as unknown as V3Command;
  }
  if (frame.type === "health" || frame.type === "shutdown") {
    if (!exact(frame, ["protocol_version", "type"])) throw new Error("invalid_frame");
    return frame as unknown as V3Command;
  }
  throw new Error("invalid_frame");
}

export function parseV3Event(line: string): V3Event {
  const frame = parseFrame(line);
  if (frame.protocol_version !== 3 || typeof frame.type !== "string") throw new Error("invalid_frame");
  if (frame.type === "ready") {
    if (!exact(frame, ["protocol_version", "type", "provider", "model_id", "api"])
      || !validId(frame.provider) || !validId(frame.model_id) || !validId(frame.api)) throw new Error("invalid_frame");
  } else if (frame.type === "health") {
    if (!exact(frame, ["protocol_version", "type", "state"])
      || !["starting", "ready", "busy", "failed", "closed"].includes(frame.state as string)) throw new Error("invalid_frame");
  } else if (frame.type === "shutdown") {
    if (!exact(frame, ["protocol_version", "type"])) throw new Error("invalid_frame");
  } else if (frame.type === "failed" && !("generation_id" in frame)) {
    if (!exact(frame, ["protocol_version", "type", "code"]) || !errorCodes.has(frame.code as ProtocolErrorCode)) throw new Error("invalid_frame");
  } else {
    if (!identity(frame)) throw new Error("invalid_frame");
    if (["accepted", "completed", "cancelled"].includes(frame.type)) {
      if (!exact(frame, ["protocol_version", "type", "generation_id", "epoch"])) throw new Error("invalid_frame");
    } else if (frame.type === "text_delta") {
      if (!exact(frame, ["protocol_version", "type", "generation_id", "epoch", "delta"])
        || typeof frame.delta !== "string" || encoder.encode(frame.delta).byteLength > PROTOCOL_LIMITS.maxDeltaBytes) throw new Error("invalid_frame");
    } else if (frame.type === "tool_calls") {
      if (!exact(frame, ["protocol_version", "type", "generation_id", "epoch", "round", "calls"])
        || !Number.isSafeInteger(frame.round) || (frame.round as number) < 1
        || !Array.isArray(frame.calls) || frame.calls.length === 0
        || frame.calls.length > TOOL_LIMITS.maxCallsPerBatch || !frame.calls.every(validCall)) throw new Error("invalid_frame");
    } else if (frame.type === "failed") {
      if (!exact(frame, ["protocol_version", "type", "generation_id", "epoch", "code"])
        || !errorCodes.has(frame.code as ProtocolErrorCode)) throw new Error("invalid_frame");
    } else throw new Error("invalid_frame");
  }
  return frame as unknown as V3Event;
}

export function encodeV3Event(event: V3Event): string {
  const line = JSON.stringify(event);
  if (encoder.encode(line).byteLength > PROTOCOL_LIMITS.maxFrameBytes) throw new Error("frame_too_large");
  return line;
}

function parseFrame(line: string): Record<string, unknown> {
  if (encoder.encode(line).byteLength > PROTOCOL_LIMITS.maxFrameBytes) throw new Error("frame_too_large");
  rejectDuplicateKeys(line);
  let value: unknown;
  try { value = JSON.parse(line); } catch { throw new Error("malformed"); }
  const frame = record(value);
  if (!frame) throw new Error("invalid_frame");
  return frame;
}

/** JSON scanner used solely to reject duplicate object keys before JSON.parse normalizes them. */
function rejectDuplicateKeys(source: string): void {
  let index = 0;
  const whitespace = () => { while (/\s/u.test(source[index] ?? "")) index += 1; };
  const string = (): string => {
    const start = index;
    if (source[index++] !== '"') throw new Error("malformed");
    while (index < source.length) {
      const character = source[index++];
      if (character === "\\") { index += 1; continue; }
      if (character === '"') return JSON.parse(source.slice(start, index)) as string;
    }
    throw new Error("malformed");
  };
  const value = (): void => {
    whitespace();
    if (source[index] === "{") {
      index += 1; whitespace();
      const keys = new Set<string>();
      if (source[index] === "}") { index += 1; return; }
      while (true) {
        whitespace(); const key = string();
        if (keys.has(key)) throw new Error("duplicate_field");
        keys.add(key); whitespace();
        if (source[index++] !== ":") throw new Error("malformed");
        value(); whitespace();
        const delimiter = source[index++];
        if (delimiter === "}") return;
        if (delimiter !== ",") throw new Error("malformed");
      }
    }
    if (source[index] === "[") {
      index += 1; whitespace();
      if (source[index] === "]") { index += 1; return; }
      while (true) {
        value(); whitespace();
        const delimiter = source[index++];
        if (delimiter === "]") return;
        if (delimiter !== ",") throw new Error("malformed");
      }
    }
    if (source[index] === '"') { string(); return; }
    while (index < source.length && !/[\s,\]}]/u.test(source[index] ?? "")) index += 1;
  };
  value(); whitespace();
  if (index !== source.length) throw new Error("malformed");
}
