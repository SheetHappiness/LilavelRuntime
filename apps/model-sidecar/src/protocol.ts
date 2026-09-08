import { TextEncoder } from "node:util";

export const PROTOCOL_VERSION = 2 as const;

export const PROTOCOL_LIMITS = {
  maxFrameBytes: 256 * 1024,
  maxPromptBytes: 64 * 1024,
  maxContextBytes: 64 * 1024,
  maxDeltaBytes: 16 * 1024,
  maxResponseBytes: 256 * 1024,
  maxIdBytes: 128,
  maxGuidanceBytes: 16 * 1024,
  maxGuidanceBlocks: 32,
} as const;

export type ProtocolErrorCode =
  | "busy"
  | "not_ready"
  | "closed"
  | "not_active"
  | "startup_error"
  | "provider_error"
  | "unsupported_output"
  | "protocol_error"
  | "output_limit"
  | "cleanup_timeout"
  | "cleanup_error"
  | "shutdown_timeout"
  | "cancellation_timeout"
  | "cancelled";

export type ProtocolCommand = GenerateCommand | CancelCommand | HealthCommand | ShutdownCommand;

export type ContextRole = "user" | "assistant";

export interface ContextMessage {
  role: ContextRole;
  text: string;
}

export interface GeneratePromptCommand {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "generate";
  generation_id: string;
  epoch: number;
  prompt: string;
  system_prompt?: string[];
}

export interface GenerateContextCommand {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "generate";
  generation_id: string;
  epoch: number;
  messages: ContextMessage[];
  system_prompt?: string[];
}

export type GenerateCommand = GeneratePromptCommand | GenerateContextCommand;

export interface CancelCommand {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "cancel";
  generation_id: string;
  epoch: number;
}

export interface HealthCommand {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "health";
}

export interface ShutdownCommand {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "shutdown";
}

export type ProtocolEvent =
  | ReadyEvent
  | HealthEvent
  | AcceptedEvent
  | TextDeltaEvent
  | CompletedEvent
  | CancelledEvent
  | FailedEvent
  | ShutdownEvent;

export interface ReadyEvent {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "ready";
  provider: string;
  model_id: string;
  api: string;
}

export interface HealthEvent {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "health";
  state: "starting" | "ready" | "busy" | "failed" | "closed";
}

export interface AcceptedEvent {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "accepted";
  generation_id: string;
  epoch: number;
}

export interface TextDeltaEvent {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "text_delta";
  generation_id: string;
  epoch: number;
  delta: string;
}

export interface CompletedEvent {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "completed";
  generation_id: string;
  epoch: number;
}

export interface CancelledEvent {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "cancelled";
  generation_id: string;
  epoch: number;
}

export type FailedEvent =
  | {
      protocol_version: typeof PROTOCOL_VERSION;
      type: "failed";
      code: ProtocolErrorCode;
    }
  | {
      protocol_version: typeof PROTOCOL_VERSION;
      type: "failed";
      generation_id: string;
      epoch: number;
      code: ProtocolErrorCode;
    };

export interface ShutdownEvent {
  protocol_version: typeof PROTOCOL_VERSION;
  type: "shutdown";
}

export class ProtocolError extends Error {
  readonly reason:
    | "malformed"
    | "unsupported_version"
    | "unknown_field"
    | "duplicate_field"
    | "invalid_field"
    | "frame_too_large";

  constructor(
    reason:
      | "malformed"
      | "unsupported_version"
      | "unknown_field"
      | "duplicate_field"
      | "invalid_field"
      | "frame_too_large",
  ) {
    super(reason);
    this.name = "ProtocolError";
    this.reason = reason;
  }
}

const encoder = new TextEncoder();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertExactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const actual = Object.keys(value);
  if (actual.length !== expected.length || actual.some((key) => !expected.includes(key))) {
    throw new ProtocolError("unknown_field");
  }
}

function assertVersion(value: Record<string, unknown>): void {
  if (value.protocol_version !== PROTOCOL_VERSION) throw new ProtocolError("unsupported_version");
}

function assertId(value: unknown): asserts value is string {
  if (typeof value !== "string" || value.length === 0 || encoder.encode(value).length > PROTOCOL_LIMITS.maxIdBytes) {
    throw new ProtocolError("invalid_field");
  }
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if (code < 0x20 || code === 0x7f) throw new ProtocolError("invalid_field");
  }
}

function assertEpoch(value: unknown): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new ProtocolError("invalid_field");
  }
}

export function validateGenerationId(value: string): void {
  assertId(value);
}

export function validateEpoch(value: number): void {
  assertEpoch(value);
}

export function validateContextMessages(value: readonly ContextMessage[]): void {
  assertContextMessages(value);
}

function assertBoundedString(value: unknown, maxBytes: number): asserts value is string {
  if (typeof value !== "string" || encoder.encode(value).length > maxBytes) {
    throw new ProtocolError("invalid_field");
  }
}

function assertContextMessages(value: unknown): asserts value is ContextMessage[] {
  if (!Array.isArray(value) || value.length === 0) throw new ProtocolError("invalid_field");
  let totalBytes = 0;
  for (const item of value) {
    if (!isRecord(item)) throw new ProtocolError("invalid_field");
    assertExactKeys(item, ["role", "text"]);
    if (item.role !== "user" && item.role !== "assistant") throw new ProtocolError("invalid_field");
    assertBoundedString(item.text, PROTOCOL_LIMITS.maxPromptBytes);
    totalBytes += new TextEncoder().encode(item.text).length;
  }
  if (totalBytes > PROTOCOL_LIMITS.maxContextBytes) throw new ProtocolError("invalid_field");
}

export function validateSystemPrompt(value: unknown): asserts value is string[] {
  if (!Array.isArray(value)) throw new ProtocolError("invalid_field");
  if (value.length > PROTOCOL_LIMITS.maxGuidanceBlocks) throw new ProtocolError("invalid_field");
  let total = 0;
  const seen = new Set<string>();
  for (const block of value) {
    if (
      typeof block !== "string" ||
      block.trim().length === 0 ||
      seen.has(block) ||
      encoder.encode(block).byteLength > PROTOCOL_LIMITS.maxPromptBytes
    ) {
      throw new ProtocolError("invalid_field");
    }
    seen.add(block);
    total += encoder.encode(block).byteLength;
  }
  if (total > PROTOCOL_LIMITS.maxGuidanceBytes) throw new ProtocolError("invalid_field");
}

function assertErrorCode(value: unknown): asserts value is ProtocolErrorCode {
  if (
    value !== "busy" &&
    value !== "not_ready" &&
    value !== "closed" &&
    value !== "not_active" &&
    value !== "startup_error" &&
    value !== "provider_error" &&
    value !== "unsupported_output" &&
    value !== "protocol_error" &&
    value !== "output_limit" &&
    value !== "cleanup_timeout" &&
    value !== "cleanup_error" &&
    value !== "shutdown_timeout" &&
    value !== "cancellation_timeout" &&
    value !== "cancelled"
  ) {
    throw new ProtocolError("invalid_field");
  }
}

function assertFrameSize(line: string): void {
  if (encoder.encode(line).length > PROTOCOL_LIMITS.maxFrameBytes) {
    throw new ProtocolError("frame_too_large");
  }
}

function assertStrictJsonSyntax(line: string): void {
  let index = 0;

  const skipWhitespace = (): void => {
    while (index < line.length && /\s/.test(line[index] ?? "")) index += 1;
  };

  const parseString = (): string => {
    const start = index;
    if (line[index] !== '"') throw new ProtocolError("malformed");
    index += 1;
    while (index < line.length) {
      const code = line.charCodeAt(index);
      if (code === 0x22) {
        index += 1;
        try {
          return JSON.parse(line.slice(start, index)) as string;
        } catch {
          throw new ProtocolError("malformed");
        }
      }
      if (code < 0x20) throw new ProtocolError("malformed");
      if (code === 0x5c) {
        index += 1;
        if (index >= line.length) throw new ProtocolError("malformed");
        if (line[index] === "u") {
          if (!/^[0-9a-fA-F]{4}$/.test(line.slice(index + 1, index + 5))) {
            throw new ProtocolError("malformed");
          }
          index += 5;
        } else {
          index += 1;
        }
      } else {
        index += 1;
      }
    }
    throw new ProtocolError("malformed");
  };

  const parseNumber = (key: string | undefined): void => {
    const start = index;
    if (line[index] === "-") index += 1;
    if (line[index] === "0") {
      index += 1;
    } else if (/[1-9]/.test(line[index] ?? "")) {
      index += 1;
      while (/\d/.test(line[index] ?? "")) index += 1;
    } else {
      throw new ProtocolError("malformed");
    }
    if (line[index] === ".") {
      index += 1;
      if (!/\d/.test(line[index] ?? "")) throw new ProtocolError("malformed");
      while (/\d/.test(line[index] ?? "")) index += 1;
    }
    if (line[index] === "e" || line[index] === "E") {
      index += 1;
      if (line[index] === "+" || line[index] === "-") index += 1;
      if (!/\d/.test(line[index] ?? "")) throw new ProtocolError("malformed");
      while (/\d/.test(line[index] ?? "")) index += 1;
    }
    if (key === "protocol_version" || key === "epoch") {
      const token = line.slice(start, index);
      if (!/^-?(?:0|[1-9]\d*)$/.test(token)) throw new ProtocolError("invalid_field");
    }
  };

  const parseValue = (key: string | undefined): void => {
    skipWhitespace();
    const character = line[index];
    if (character === '"') {
      parseString();
      return;
    }
    if (character === "{") {
      index += 1;
      skipWhitespace();
      const keys = new Set<string>();
      if (line[index] === "}") {
        index += 1;
        return;
      }
      while (true) {
        skipWhitespace();
        const objectKey = parseString();
        if (keys.has(objectKey)) throw new ProtocolError("duplicate_field");
        keys.add(objectKey);
        skipWhitespace();
        if (line[index] !== ":") throw new ProtocolError("malformed");
        index += 1;
        parseValue(objectKey);
        skipWhitespace();
        if (line[index] === "}") {
          index += 1;
          return;
        }
        if (line[index] !== ",") throw new ProtocolError("malformed");
        index += 1;
      }
    }
    if (character === "[") {
      index += 1;
      skipWhitespace();
      if (line[index] === "]") {
        index += 1;
        return;
      }
      while (true) {
        parseValue(undefined);
        skipWhitespace();
        if (line[index] === "]") {
          index += 1;
          return;
        }
        if (line[index] !== ",") throw new ProtocolError("malformed");
        index += 1;
      }
    }
    if (character === "-" || /\d/.test(character ?? "")) {
      parseNumber(key);
      return;
    }
    for (const literal of ["true", "false", "null"]) {
      if (line.startsWith(literal, index)) {
        index += literal.length;
        return;
      }
    }
    throw new ProtocolError("malformed");
  };

  parseValue(undefined);
  skipWhitespace();
  if (index !== line.length) throw new ProtocolError("malformed");
}

export function parseCommand(line: string): ProtocolCommand {
  assertFrameSize(line);
  let value: unknown;
  try {
    assertStrictJsonSyntax(line);
    value = JSON.parse(line);
  } catch (error) {
    if (error instanceof ProtocolError) throw error;
    throw new ProtocolError("malformed");
  }
  if (!isRecord(value)) throw new ProtocolError("malformed");
  assertVersion(value);

  if (value.type === "generate") {
    assertId(value.generation_id);
    assertEpoch(value.epoch);
    if ("prompt" in value && !("messages" in value)) {
      assertExactKeys(value, ["protocol_version", "type", "generation_id", "epoch", "prompt", ...( "system_prompt" in value ? ["system_prompt"] : [])]);
      assertBoundedString(value.prompt, PROTOCOL_LIMITS.maxPromptBytes);
      if ("system_prompt" in value) validateSystemPrompt(value.system_prompt as string[]);
      return value as unknown as GeneratePromptCommand;
    }
    if ("messages" in value && !("prompt" in value)) {
      assertExactKeys(value, ["protocol_version", "type", "generation_id", "epoch", "messages", ...( "system_prompt" in value ? ["system_prompt"] : [])]);
      assertContextMessages(value.messages);
      if ("system_prompt" in value) validateSystemPrompt(value.system_prompt as string[]);
      return value as unknown as GenerateContextCommand;
    }
    throw new ProtocolError("unknown_field");
  }
  if (value.type === "cancel") {
    assertExactKeys(value, ["protocol_version", "type", "generation_id", "epoch"]);
    assertId(value.generation_id);
    assertEpoch(value.epoch);
    return value as unknown as CancelCommand;
  }
  if (value.type === "health") {
    assertExactKeys(value, ["protocol_version", "type"]);
    return value as unknown as HealthCommand;
  }
  if (value.type === "shutdown") {
    assertExactKeys(value, ["protocol_version", "type"]);
    return value as unknown as ShutdownCommand;
  }
  throw new ProtocolError("invalid_field");
}

function validateEvent(event: ProtocolEvent): void {
  const value = event as unknown as Record<string, unknown>;
  assertVersion(value);
  const eventType = value.type;
  if (typeof eventType !== "string") throw new ProtocolError("invalid_field");
  switch (eventType) {
    case "ready":
      assertExactKeys(value, ["protocol_version", "type", "provider", "model_id", "api"]);
      assertBoundedString(value.provider, PROTOCOL_LIMITS.maxIdBytes);
      assertBoundedString(value.model_id, PROTOCOL_LIMITS.maxIdBytes);
      assertBoundedString(value.api, PROTOCOL_LIMITS.maxIdBytes);
      return;
    case "health":
      assertExactKeys(value, ["protocol_version", "type", "state"]);
      if (
        value.state !== "starting" &&
        value.state !== "ready" &&
        value.state !== "busy" &&
        value.state !== "failed" &&
        value.state !== "closed"
      ) {
        throw new ProtocolError("invalid_field");
      }
      return;
    case "accepted":
    case "completed":
    case "cancelled":
      assertExactKeys(value, ["protocol_version", "type", "generation_id", "epoch"]);
      assertId(value.generation_id);
      assertEpoch(value.epoch);
      return;
    case "text_delta":
      assertExactKeys(value, ["protocol_version", "type", "generation_id", "epoch", "delta"]);
      assertId(value.generation_id);
      assertEpoch(value.epoch);
      assertBoundedString(value.delta, PROTOCOL_LIMITS.maxDeltaBytes);
      if (value.delta.length === 0) throw new ProtocolError("invalid_field");
      return;
    case "failed":
      if ("generation_id" in value || "epoch" in value) {
        assertExactKeys(value, ["protocol_version", "type", "generation_id", "epoch", "code"]);
        assertId(value.generation_id);
        assertEpoch(value.epoch);
      } else {
        assertExactKeys(value, ["protocol_version", "type", "code"]);
      }
      assertErrorCode(value.code);
      return;
    case "shutdown":
      assertExactKeys(value, ["protocol_version", "type"]);
      return;
    default:
      throw new ProtocolError("invalid_field");
  }
}

export function encodeFrame(frame: ProtocolCommand | ProtocolEvent): string {
  const line = JSON.stringify(frame);
  assertFrameSize(line);
  return `${line}\n`;
}

export function encodeCommand(command: ProtocolCommand): string {
  if (command.type === "generate" && "messages" in command) {
    validateContextMessages(command.messages);
  }
  parseCommand(JSON.stringify(command));
  return encodeFrame(command);
}

export function encodeEvent(event: ProtocolEvent): string {
  validateEvent(event);
  return encodeFrame(event);
}

export async function* readProtocolLines(
  input: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<string, void, undefined> {
  const reader = input.getReader();
  let buffered = new Uint8Array(0);
  let abortListener: (() => void) | undefined;
  const aborted = signal
    ? new Promise<undefined>((resolve) => {
        const abort = (): void => {
          void reader.cancel().catch(() => undefined);
          resolve(undefined);
        };
        abortListener = abort;
        if (signal.aborted) abort();
        else signal.addEventListener("abort", abort, { once: true });
      })
    : undefined;
  try {
    while (true) {
      const readResult = aborted ? await Promise.race([reader.read(), aborted]) : await reader.read();
      if (readResult === undefined) return;
      const { done, value } = readResult;
      if (done) break;
      const chunk = value;
      let lineStart = 0;
      for (let index = 0; index < chunk.length; index += 1) {
        if (chunk[index] !== 0x0a) continue;
        const linePart = chunk.subarray(lineStart, index);
        const lineBytes = new Uint8Array(buffered.length + linePart.length);
        lineBytes.set(buffered);
        lineBytes.set(linePart, buffered.length);
        buffered = new Uint8Array(0);
        if (lineBytes.length > PROTOCOL_LIMITS.maxFrameBytes) {
          throw new ProtocolError("frame_too_large");
        }
        lineStart = index + 1;
        if (lineBytes.at(-1) === 0x0d) yield decodeLine(lineBytes.subarray(0, lineBytes.length - 1));
        else yield decodeLine(lineBytes);
      }
      const remainder = chunk.subarray(lineStart);
      if (remainder.length > 0) {
        if (buffered.length + remainder.length > PROTOCOL_LIMITS.maxFrameBytes) {
          throw new ProtocolError("frame_too_large");
        }
        const next = new Uint8Array(buffered.length + remainder.length);
        next.set(buffered);
        next.set(remainder, buffered.length);
        buffered = next;
      }
    }
    if (buffered.length > 0) throw new ProtocolError("malformed");
  } finally {
    if (signal && abortListener) signal.removeEventListener("abort", abortListener);
    reader.releaseLock();
  }
}

function decodeLine(bytes: Uint8Array): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new ProtocolError("malformed");
  }
}
