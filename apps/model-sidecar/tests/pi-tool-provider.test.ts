import { describe, expect, test } from "bun:test";
import type { Context } from "@oh-my-pi/pi-ai";
import { toolChoiceForTurn } from "../src/pi-tool-provider.js";

const userContext = (): Context => ({
  messages: [{ role: "user", content: "proof", timestamp: 1 }],
});

describe("explicit live-proof tool choice", () => {
  test("leaves the ordinary provider path unset", () => {
    expect(toolChoiceForTurn(userContext(), {})).toBeUndefined();
  });

  test("requires a tool only on the first harness turn", () => {
    expect(toolChoiceForTurn(userContext(), { requireToolOnFirstTurn: true })).toBe("required");
    const continuation: Context = {
      messages: [
        ...userContext().messages,
        {
          role: "toolResult",
          toolCallId: "call-1",
          toolName: "discord_send_message",
          content: [{ type: "text", text: "{\"status\":\"ok\"}" }],
          isError: false,
          timestamp: 2,
        },
      ],
    };
    expect(toolChoiceForTurn(continuation, { requireToolOnFirstTurn: true })).toBeUndefined();
  });
});
