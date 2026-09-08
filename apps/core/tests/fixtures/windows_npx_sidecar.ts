// Deliberately ignores cancellation and shutdown so Core's bounded
// cancellation watchdog must contain the real npx -> Bun process tree.

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function writeFrame(frame: Record<string, unknown>): void {
  process.stdout.write(`${JSON.stringify(frame)}\n`);
}

writeFrame({
  protocol_version: 2,
  type: "ready",
  provider: "openai-codex",
  model_id: "gpt-5.6-luna",
  api: "openai-codex-responses",
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
    };
    if (command.type === "generate" && command.generation_id && command.epoch !== undefined) {
      writeFrame({
        protocol_version: 2,
        type: "accepted",
        generation_id: command.generation_id,
        epoch: command.epoch,
      });
      const delta = encoder.encode("WINDOWS_CONTAINMENT_PROBE");
      writeFrame({
        protocol_version: 2,
        type: "text_delta",
        generation_id: command.generation_id,
        epoch: command.epoch,
        delta: new TextDecoder().decode(delta),
      });
      // Keep the request active.  The fixture intentionally does not emit a
      // terminal frame for cancel or shutdown; Core must fail closed and use
      // process containment when its cancellation deadline expires.
    }
  }
}
