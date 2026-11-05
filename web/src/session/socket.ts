import { parseServerMessage, type ClientMessage, type ServerMessage } from "../contracts/session";

export interface SocketEvents {
  onMessage(msg: ServerMessage): void;
  onStatus?(status: "connecting" | "open" | "closed"): void;
}

/**
 * WebSocket for /ws/session/{id}. Sends `hello` on every (re)connect, queues messages while
 * disconnected, and reconnects with backoff unless closed deliberately.
 */
export class SessionSocket {
  private ws: WebSocket | null = null;
  private queue: string[] = [];
  private closed = false;
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  /** Every frame sent and received, for the debug panel and tests. */
  readonly log: { dir: "in" | "out"; msg: unknown; t: number }[] = [];

  constructor(
    private url: string,
    private hello: () => ClientMessage,
    private ev: SocketEvents,
  ) {}

  connect(): void {
    this.closed = false;
    this.open();
  }

  send(msg: ClientMessage): void {
    this.record("out", msg);
    const data = JSON.stringify(msg);
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(data);
    else this.queue.push(data);
  }

  close(): void {
    this.closed = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.ws?.close();
    this.ws = null;
  }

  private open() {
    this.ev.onStatus?.("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      const hello = this.hello();
      this.record("out", hello);
      ws.send(JSON.stringify(hello));
      for (const q of this.queue.splice(0)) ws.send(q);
      this.ev.onStatus?.("open");
    };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") return;
      const msg = parseServerMessage(e.data);
      if (!msg) {
        console.warn("ignoring unknown server frame", e.data.slice(0, 200));
        return;
      }
      this.record("in", msg.type === "tts_chunk" ? { ...msg, data_b64: `<${msg.data_b64.length}b>` } : msg);
      this.ev.onMessage(msg);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.ev.onStatus?.("closed");
      if (!this.closed) {
        const delay = Math.min(10_000, 500 * 2 ** this.attempt++);
        this.retryTimer = setTimeout(() => this.open(), delay);
      }
    };
  }

  private record(dir: "in" | "out", msg: unknown) {
    const m =
      dir === "out" && (msg as { type?: string }).type === "utterance_audio"
        ? { ...(msg as object), data_b64: "<audio>" }
        : msg;
    this.log.push({ dir, msg: m, t: Date.now() });
    if (this.log.length > 500) this.log.splice(0, 100);
  }
}
