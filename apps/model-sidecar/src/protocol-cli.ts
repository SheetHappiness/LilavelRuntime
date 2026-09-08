import { runProtocolServer } from "./server.js";

try {
  process.exitCode = await runProtocolServer();
} catch {
  console.error("Lilavel protocol host failed.");
  process.exitCode = 1;
}
