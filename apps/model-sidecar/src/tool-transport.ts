import type { RawSseEvent, Tool, ToolCall as PiToolCall } from "@oh-my-pi/pi-ai";

export const TOOL_LIMITS = {
  maxExposedTools: 16,
  maxDefinitionBytes: 32 * 1024,
  maxCallsPerBatch: 4,
  maxCallsPerGeneration: 8,
  maxRoundsPerGeneration: 4,
  maxArgumentsBytes: 16 * 1024,
  maxResultBytes: 16 * 1024,
  maxTransientContextBytes: 256 * 1024,
} as const;

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export interface LilavelToolSpec {
  readonly name: string;
  readonly description: string;
  readonly input_schema: Readonly<Record<string, JsonValue>>;
}

export interface FinalToolCall {
  readonly local_call_id: string;
  readonly tool_name: string;
  readonly arguments: Readonly<Record<string, JsonValue>> | null;
  readonly argument_error: "invalid_json" | "invalid_shape" | null;
}

interface RawFunctionCall {
  readonly providerId: string;
  readonly providerName: string;
  readonly rawArguments: string;
}

export class ToolTransportError extends Error {
  constructor(readonly code: "alias_collision" | "bounds" | "correspondence" | "duplicate_provider_id") {
    super(code);
    this.name = "ToolTransportError";
  }
}

function bytes(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function isObject(value: unknown): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function jsonEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/**
 * V3 keeps provider-safe aliases transport-local. A provider alias is never
 * presented as an application capability or written to a Core-facing frame.
 */
export function mapTools(specs: readonly LilavelToolSpec[]): {
  readonly tools: Tool[];
  readonly applicationByProviderAlias: ReadonlyMap<string, string>;
} {
  if (specs.length > TOOL_LIMITS.maxExposedTools) throw new ToolTransportError("bounds");
  const aliases = new Map<string, string>();
  let total = 0;
  const tools = specs.map((spec) => {
    const alias = spec.name.replace(/[^A-Za-z0-9_]/g, "_");
    if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(alias)) throw new ToolTransportError("bounds");
    if (aliases.has(alias)) throw new ToolTransportError("alias_collision");
    aliases.set(alias, spec.name);
    const serialized = JSON.stringify({ name: alias, description: spec.description, parameters: spec.input_schema });
    total += bytes(serialized);
    return { name: alias, description: spec.description, parameters: spec.input_schema, strict: false } as Tool;
  });
  if (total > TOOL_LIMITS.maxDefinitionBytes) throw new ToolTransportError("bounds");
  return { tools, applicationByProviderAlias: aliases };
}

/**
 * Captures only ordinary Responses function-call terminal items. `onSseEvent`
 * is a pinned pi-ai diagnostic hook; its payload never leaves this sidecar.
 */
export class RawFunctionCallCollector {
  readonly #calls = new Map<string, RawFunctionCall>();
  #bytes = 0;

  observe(event: RawSseEvent): void {
    if (event.event !== "response.output_item.done") return;
    this.#bytes += bytes(event.data);
    if (this.#bytes > TOOL_LIMITS.maxTransientContextBytes) throw new ToolTransportError("bounds");
    let value: unknown;
    try {
      value = JSON.parse(event.data);
    } catch {
      throw new ToolTransportError("correspondence");
    }
    if (!isObject(value) || !isObject(value.item) || value.item.type !== "function_call") return;
    const item = value.item;
    if (
      typeof item.call_id !== "string" ||
      typeof item.id !== "string" ||
      typeof item.name !== "string" ||
      typeof item.arguments !== "string"
    ) {
      throw new ToolTransportError("correspondence");
    }
    const providerId = `${item.call_id}|${item.id}`;
    if (this.#calls.has(providerId)) throw new ToolTransportError("duplicate_provider_id");
    this.#calls.set(providerId, { providerId, providerName: item.name, rawArguments: item.arguments });
  }

  take(providerId: string): RawFunctionCall | undefined {
    const call = this.#calls.get(providerId);
    if (call) this.#calls.delete(providerId);
    return call;
  }

  get pendingCount(): number {
    return this.#calls.size;
  }
}

/** A generation-local mapping. It is discarded when the transport turn ends. */
export class GenerationCallMap {
  readonly #providerToLocal = new Map<string, string>();
  readonly #localToProvider = new Map<string, string>();
  #next = 1;

  assign(providerId: string): string {
    if (this.#providerToLocal.has(providerId)) throw new ToolTransportError("duplicate_provider_id");
    const local = `call-${this.#next}`;
    this.#next += 1;
    this.#providerToLocal.set(providerId, local);
    this.#localToProvider.set(local, providerId);
    return local;
  }

  localFor(providerId: string): string | undefined {
    return this.#providerToLocal.get(providerId);
  }

  providerFor(localId: string): string | undefined {
    return this.#localToProvider.get(localId);
  }
}

/**
 * Accepts pi-ai calls only after the provider turn has finalized. Validation is
 * based on the raw terminal native item, not pi-ai's repair-capable arguments.
 */
export function finalizeOrdinaryFunctionCalls(
  calls: readonly PiToolCall[],
  collector: RawFunctionCallCollector,
  map: GenerationCallMap,
  aliases: ReadonlyMap<string, string>,
): FinalToolCall[] {
  if (calls.length === 0 || calls.length > TOOL_LIMITS.maxCallsPerBatch) throw new ToolTransportError("bounds");
  const finalized: FinalToolCall[] = [];
  const seen = new Set<string>();
  for (const call of calls) {
    if (seen.has(call.id)) throw new ToolTransportError("duplicate_provider_id");
    seen.add(call.id);
    const raw = collector.take(call.id);
    const toolName = aliases.get(call.name);
    if (!raw || !toolName || raw.providerName !== call.name) throw new ToolTransportError("correspondence");
    const localCallId = map.assign(call.id);
    if (bytes(raw.rawArguments) > TOOL_LIMITS.maxArgumentsBytes) throw new ToolTransportError("bounds");
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw.rawArguments);
    } catch {
      finalized.push({ local_call_id: localCallId, tool_name: toolName, arguments: null, argument_error: "invalid_json" });
      continue;
    }
    if (!isObject(parsed)) {
      finalized.push({ local_call_id: localCallId, tool_name: toolName, arguments: null, argument_error: "invalid_shape" });
      continue;
    }
    if (!isObject(call.arguments) || !jsonEqual(parsed, call.arguments)) throw new ToolTransportError("correspondence");
    finalized.push({ local_call_id: localCallId, tool_name: toolName, arguments: parsed, argument_error: null });
  }
  return finalized;
}
