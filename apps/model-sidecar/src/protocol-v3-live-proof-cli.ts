import { createPiToolProvider } from "./pi-tool-provider.js";
import { runProtocolV3Server } from "./protocol-v3-server.js";

// Explicit live-proof harness only. The ordinary protocol:v3 entry point keeps
// its provider-default tool choice (auto/unset).
process.exitCode = await runProtocolV3Server({
  createProvider: () => createPiToolProvider({ requireToolOnFirstTurn: true }),
});
