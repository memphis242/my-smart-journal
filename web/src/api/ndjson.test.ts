import { describe, expect, it } from 'vitest';
import { NdjsonLineError, readNdjson } from './ndjson';

function streamOf(chunks: readonly Uint8Array[]): { stream: ReadableStream<Uint8Array>; cancelled: () => boolean } {
  let cancelled = false;
  let index = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks[index++];
      if (chunk === undefined) controller.close();
      else controller.enqueue(chunk);
    },
    cancel() {
      cancelled = true;
    },
  });
  return { stream, cancelled: () => cancelled };
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<unknown[]> {
  const out: unknown[] = [];
  for await (const value of readNdjson(stream)) out.push(value);
  return out;
}

describe('readNdjson', () => {
  it('reassembles lines split across chunks, including inside a multi-byte character', async () => {
    const bytes = new TextEncoder().encode('{"tier":1,"name":"café"}\n\n{"done":true}\n');
    // Split inside the two-byte "e acute" and inside the second line.
    const cut = bytes.indexOf(0xc3) + 1;
    const { stream } = streamOf([bytes.slice(0, cut), bytes.slice(cut, cut + 9), bytes.slice(cut + 9)]);
    expect(await collect(stream)).toEqual([{ tier: 1, name: 'café' }, { done: true }]);
  });

  it('yields a final line that has no trailing newline', async () => {
    const { stream } = streamOf([new TextEncoder().encode('{"a":1}\n{"b":2}')]);
    expect(await collect(stream)).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('names the line number of malformed JSON', async () => {
    const { stream } = streamOf([new TextEncoder().encode('{"a":1}\n{"b":\n')]);
    const error = await collect(stream).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(NdjsonLineError);
    expect((error as NdjsonLineError).line).toBe(2);
  });

  it('cancels the stream when the consumer stops early', async () => {
    const { stream, cancelled } = streamOf([new TextEncoder().encode('{"a":1}\n'), new TextEncoder().encode('{"b":2}\n')]);
    for await (const value of readNdjson(stream)) {
      expect(value).toEqual({ a: 1 });
      break;
    }
    expect(cancelled()).toBe(true);
  });
});
