import { describe, expect, it } from "vitest";
import { resolveWsUrl } from "./api";

describe("resolveWsUrl", () => {
  const origin = "https://abc.trycloudflare.com";
  it("same-origin mode uses the page origin, whatever host the backend put in ws_url", () => {
    const g = globalThis as unknown as { window?: unknown };
    const prev = g.window;
    g.window = { location: { origin } };
    try {
      expect(resolveWsUrl("ws://localhost:8000/ws/session/se_1", "tok", "")).toBe(
        "wss://abc.trycloudflare.com/ws/session/se_1?token=tok",
      );
      expect(resolveWsUrl("/ws/session/se_1", null, "")).toBe("wss://abc.trycloudflare.com/ws/session/se_1");
    } finally {
      g.window = prev;
    }
  });
  it("with an API base, an absolute ws_url is used as given", () => {
    expect(resolveWsUrl("wss://api.example.com/ws/session/se_1", "t", "https://api.example.com")).toBe(
      "wss://api.example.com/ws/session/se_1?token=t",
    );
    expect(resolveWsUrl("/ws/session/se_1", null, "http://localhost:8000")).toBe("ws://localhost:8000/ws/session/se_1");
  });
});
