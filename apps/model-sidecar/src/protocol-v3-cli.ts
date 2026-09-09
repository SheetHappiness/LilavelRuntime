import { runProtocolV3Server } from "./protocol-v3-server.js";

try {
  process.exitCode = await runProtocolV3Server();
} catch {
  console.error("Lilavel V3 protocol host failed.");
  process.exitCode = 1;
}
