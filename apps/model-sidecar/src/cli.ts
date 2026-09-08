import { createModelSidecar } from "./index.js";

const args = process.argv.slice(2);
const probe = args[0] === "--probe" ? args[1] : undefined;
const prompt = (probe ? args.slice(2) : args).join(" ");
let sidecar: ReturnType<typeof createModelSidecar>;
let activeRequestId: string | undefined;
let cancellationRequested = false;

sidecar = createModelSidecar({
  onEvent: (event) => {
    console.log(JSON.stringify(event));
    if (probe === "cancel-recovery" && event.type === "text_delta" && event.delta.length > 0 && !cancellationRequested) {
      cancellationRequested = true;
      sidecar.cancel(event.requestId ?? "");
    }
  },
});

process.once("SIGINT", () => {
  if (activeRequestId) sidecar.cancel(activeRequestId);
});

async function runSmoke(): Promise<void> {
  await sidecar.start();
  const handle = sidecar.generate(prompt || "Reply exactly OK.");
  activeRequestId = handle.requestId;
  const result = await handle.result;
  process.exitCode = result.status === "cancelled" ? 130 : 0;
}

async function runCancellationRecoveryProbe(): Promise<void> {
  await sidecar.start();
  const cancelled = sidecar.generate(
    "Write a long response of at least 300 words beginning with CANCEL_STREAM_OK. The response is intentionally cancelled after its first text delta.",
  );
  activeRequestId = cancelled.requestId;
  const cancelledResult = await cancelled.result;

  const recovery = sidecar.generate("Reply exactly RECOVERY_OK.");
  activeRequestId = recovery.requestId;
  const recoveryResult = await recovery.result;
  const passed =
    cancellationRequested &&
    cancelledResult.status === "cancelled" &&
    recoveryResult.status === "completed" &&
    recoveryResult.text.includes("RECOVERY_OK");
  console.log(
    JSON.stringify({
      type: "probe_result",
      probe: "cancel-recovery",
      status: passed ? "passed" : "failed",
      cancellationRequestId: cancelled.requestId,
      recoveryRequestId: recovery.requestId,
      recoveryTextLength: recoveryResult.text.length,
    }),
  );
  if (!passed) process.exitCode = 1;
}

async function runIsolationProbe(): Promise<void> {
  await sidecar.start();
  const remembered = sidecar.generate("Remember the private test word quartz. Reply exactly STORED.");
  const rememberedResult = await remembered.result;
  const independent = sidecar.generate("Reply exactly UNKNOWN. You have no access to any other independent request.");
  const independentResult = await independent.result;
  const leaked = /\bquartz\b/i.test(independentResult.text);
  const passed = rememberedResult.status === "completed" && independentResult.status === "completed" && !leaked;
  console.log(
    JSON.stringify({
      type: "probe_result",
      probe: "isolation",
      status: passed ? "passed" : "failed",
      firstRequestId: remembered.requestId,
      secondRequestId: independent.requestId,
      secondTextLength: independentResult.text.length,
      leaked,
    }),
  );
  if (!passed) process.exitCode = 1;
}

try {
  if (probe === undefined) await runSmoke();
  else if (probe === "cancel-recovery") await runCancellationRecoveryProbe();
  else if (probe === "isolation") await runIsolationProbe();
  else {
    console.error("Unknown probe");
    process.exitCode = 2;
  }
} catch {
  process.exitCode = 1;
} finally {
  await sidecar.close();
}
