const decoder = new TextDecoder();

function emit(frame: Record<string, unknown>): void {
  process.stdout.write(`${JSON.stringify(frame)}\n`);
}

emit({
  protocol_version: 2,
  type: "ready",
  provider: "fixture",
  model_id: "guided-fixture",
  api: "fixture-api",
});

let buffered = "";
for await (const chunk of Bun.stdin.stream()) {
  buffered += decoder.decode(chunk, { stream: true });
  while (true) {
    const newline = buffered.indexOf("\n");
    if (newline < 0) break;
    const line = buffered.slice(0, newline).replace(/\r$/, "");
    buffered = buffered.slice(newline + 1);
    if (line.length === 0) continue;
    const command = JSON.parse(line) as {
      type?: string;
      generation_id?: string;
      epoch?: number;
      system_prompt?: unknown;
    };
    if (command.type === "generate" && command.generation_id && command.epoch !== undefined) {
      const guided =
        Array.isArray(command.system_prompt) &&
        command.system_prompt.length === 1 &&
        command.system_prompt[0] === "trusted identity";
      emit({
        protocol_version: 2,
        type: "accepted",
        generation_id: command.generation_id,
        epoch: command.epoch,
      });
      emit({
        protocol_version: 2,
        type: "text_delta",
        generation_id: command.generation_id,
        epoch: command.epoch,
        delta: guided ? "GUIDANCE_WIRE_OK" : "GUIDANCE_WIRE_MISSING",
      });
      emit({
        protocol_version: 2,
        type: "completed",
        generation_id: command.generation_id,
        epoch: command.epoch,
      });
    } else if (command.type === "shutdown") {
      emit({ protocol_version: 2, type: "shutdown" });
      process.exit(0);
    }
  }
}
