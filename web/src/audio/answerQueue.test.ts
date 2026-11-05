import { beforeAll, describe, expect, it } from "vitest";
import { AnswerQueue } from "./answerQueue";
import type { AudioOutput, PlayResult } from "./output";

/** Fake output: records what was played; each play resolves when the test calls finish(). */
class FakeOutput {
  played: string[] = [];
  owner: "brief" | "answer" | null = null;
  private resolve: ((r: PlayResult) => void) | null = null;
  play(src: string, _start = 0, owner: "brief" | "answer" = "brief") {
    this.resolve?.("stopped");
    this.played.push(src);
    this.owner = owner;
    return new Promise<PlayResult>((r) => (this.resolve = r));
  }
  stop() {
    this.owner = null;
    const r = this.resolve;
    this.resolve = null;
    r?.("stopped");
  }
  finish() {
    const r = this.resolve;
    this.resolve = null;
    this.owner = null;
    r?.("ended");
  }
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const chunk = (seq: number, sid = `t1-${seq}`) => ({
  type: "tts_chunk" as const,
  seq,
  format: "mp3",
  data_b64: btoa(`audio-${seq}`),
  sentence_id: sid,
});

beforeAll(() => {
  let n = 0;
  const map = new Map<string, number>();
  URL.createObjectURL = () => `blob:${++n}`;
  URL.revokeObjectURL = (u: string) => void map.delete(u);
});

describe("AnswerQueue", () => {
  it("plays chunks in seq order even when they arrive out of order", async () => {
    const out = new FakeOutput();
    const sentences: string[] = [];
    const q = new AnswerQueue(out as unknown as AudioOutput, { onSentence: (s) => sentences.push(s) });
    q.push(chunk(0));
    q.push(chunk(2));
    q.push(chunk(1));
    await tick();
    out.finish();
    await tick();
    out.finish();
    await tick();
    out.finish();
    await tick();
    expect(sentences).toEqual(["t1-0", "t1-1", "t1-2"]);
    expect(q.busy).toBe(false);
  });

  it("clear() stops the current chunk and drops the rest", async () => {
    const out = new FakeOutput();
    let idle = 0;
    const q = new AnswerQueue(out as unknown as AudioOutput, { onIdle: () => idle++ });
    q.push(chunk(0));
    q.push(chunk(1));
    await tick();
    expect(out.owner).toBe("answer");
    const waited = q.whenIdle();
    q.clear();
    await waited;
    await tick();
    expect(out.owner).toBeNull();
    expect(out.played).toHaveLength(1);
    expect(q.busy).toBe(false);
    expect(idle).toBe(0); // clear is not a natural end
  });

  it("drops queued chunks when something else takes the output", async () => {
    const out = new FakeOutput();
    const q = new AnswerQueue(out as unknown as AudioOutput);
    q.push(chunk(0));
    q.push(chunk(1));
    await tick();
    void out.play("brief.mp3", 0, "brief"); // brief resumes
    await tick();
    expect(q.busy).toBe(false);
    expect(out.played).toHaveLength(2); // chunk 0, then the brief; chunk 1 never played
    expect(out.played[1]).toBe("brief.mp3");
  });
});
