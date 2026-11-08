/** Incremental reader for newline-delimited JSON response bodies. */

export class NdjsonLineError extends Error {
  override readonly name = 'NdjsonLineError';
  readonly line: number;
  readonly text: string;

  constructor(line: number, text: string, cause: unknown) {
    const shown = text.length > 120 ? `${text.slice(0, 120)}...` : text;
    super(`line ${line} is not valid JSON: ${shown}`, { cause });
    this.line = line;
    this.text = shown;
  }
}

function parseLine(text: string, line: number): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch (error) {
    throw new NdjsonLineError(line, text, error);
  }
}

/**
 * Yields one parsed value per non-empty line as soon as the line is complete.
 * A final line without a trailing newline is still yielded. Stopping the
 * iteration early cancels the underlying stream.
 */
export async function* readNdjson(stream: ReadableStream<Uint8Array>): AsyncGenerator<unknown, void, undefined> {
  const reader = stream.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: false });
  let buffer = '';
  let line = 0;
  let finished = false;
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) {
        finished = true;
        break;
      }
      buffer += decoder.decode(chunk.value, { stream: true });
      let newline = buffer.indexOf('\n');
      while (newline >= 0) {
        const text = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        line += 1;
        if (text !== '') yield parseLine(text, line);
        newline = buffer.indexOf('\n');
      }
    }
    buffer += decoder.decode();
    const rest = buffer.trim();
    if (rest !== '') yield parseLine(rest, line + 1);
  } finally {
    if (!finished) {
      // Early exit or error: release the network stream; a failed cancel has nothing left to clean up.
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
}
