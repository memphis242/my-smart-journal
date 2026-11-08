/** In-browser audio recording with MediaRecorder and a live input level. */

import { AppError } from '../api/errors';
import { assert } from './assert';

export interface Recording {
  readonly blob: Blob;
  /** Bare MIME type for the upload, without codec parameters. */
  readonly mimeType: string;
  readonly extension: string;
  readonly durationMs: number;
}

const CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'] as const;
const EXTENSIONS: Readonly<Record<string, string>> = {
  'audio/webm': 'webm',
  'audio/mp4': 'm4a',
  'audio/ogg': 'ogg',
};

/** Upload types the server accepts (backend `_AUDIO_TYPES`). */
export const ACCEPTED_AUDIO_TYPES = ['audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp4', 'audio/webm', 'audio/ogg', 'audio/flac', 'audio/x-flac'] as const;

/** Types browsers report for audio files that the server knows under another name. */
const TYPE_ALIASES: Readonly<Record<string, string>> = {
  'video/webm': 'audio/webm',
  'video/mp4': 'audio/mp4',
  'audio/x-m4a': 'audio/mp4',
  'audio/m4a': 'audio/mp4',
  'audio/mp3': 'audio/mpeg',
  'audio/x-mpeg': 'audio/mpeg',
  'audio/wave': 'audio/wav',
  'audio/vnd.wave': 'audio/wav',
  'application/ogg': 'audio/ogg',
  'audio/opus': 'audio/ogg',
};

const EXTENSION_TYPES: Readonly<Record<string, string>> = {
  webm: 'audio/webm',
  weba: 'audio/webm',
  m4a: 'audio/mp4',
  mp4: 'audio/mp4',
  ogg: 'audio/ogg',
  oga: 'audio/ogg',
  opus: 'audio/ogg',
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  flac: 'audio/flac',
};

/**
 * The type to upload a chosen file as, or null when it is not audio the server
 * accepts. Browsers label files by extension and often pick a video or vendor
 * type (Chrome: `.webm` is video/webm, `.m4a` is audio/x-m4a).
 */
export function uploadAudioType(fileName: string, declaredType: string): string | null {
  const declared = bareMimeType(declaredType);
  if ((ACCEPTED_AUDIO_TYPES as readonly string[]).includes(declared)) return declared;
  const alias = TYPE_ALIASES[declared];
  if (alias !== undefined) return alias;
  const extension = /\.([a-z0-9]+)$/i.exec(fileName)?.[1]?.toLowerCase();
  return extension === undefined ? null : (EXTENSION_TYPES[extension] ?? null);
}

export function bareMimeType(mimeType: string): string {
  return (mimeType.split(';')[0] ?? mimeType).trim().toLowerCase();
}

export function extensionFor(mimeType: string): string {
  return EXTENSIONS[bareMimeType(mimeType)] ?? 'webm';
}

function pickMimeType(): string | null {
  for (const candidate of CANDIDATES) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return null;
}

/** Explains why recording cannot start here, or null when it can. */
export function recordingUnsupported(): AppError | null {
  if (!window.isSecureContext) {
    return new AppError({
      code: 'INSECURE_CONTEXT',
      message: 'Recording is unavailable: browsers allow the microphone only on HTTPS or localhost.',
      hint: 'Open the journal over HTTPS, or upload an audio file instead.',
      details: [{ label: 'Origin', value: window.location.origin }],
    });
  }
  if (typeof navigator.mediaDevices === 'undefined' || typeof navigator.mediaDevices.getUserMedia !== 'function') {
    return new AppError({
      code: 'MEDIA_DEVICES_UNSUPPORTED',
      message: 'Recording is unavailable: this browser has no microphone access API.',
      hint: 'Use a current browser, or upload an audio file instead.',
    });
  }
  if (typeof MediaRecorder === 'undefined') {
    return new AppError({
      code: 'MEDIA_RECORDER_UNSUPPORTED',
      message: 'Recording is unavailable: this browser cannot record audio (no MediaRecorder).',
      hint: 'Use a current browser, or upload an audio file instead.',
    });
  }
  return null;
}

function microphoneError(error: unknown): AppError {
  const name = error instanceof DOMException ? error.name : 'Error';
  const detail = [{ label: 'Cause', value: error instanceof Error ? `${error.name}: ${error.message}` : String(error) }];
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return new AppError({
        code: 'MIC_PERMISSION_DENIED',
        message: 'Recording did not start: microphone access was denied.',
        hint: 'Allow the microphone for this site in the browser settings (the icon left of the address), then try again.',
        details: detail,
        cause: error,
      });
    case 'NotFoundError':
    case 'OverconstrainedError':
      return new AppError({
        code: 'MIC_NOT_FOUND',
        message: 'Recording did not start: no microphone was found.',
        hint: 'Connect a microphone or check that the system has one enabled.',
        details: detail,
        cause: error,
      });
    case 'NotReadableError':
    case 'AbortError':
      return new AppError({
        code: 'MIC_BUSY',
        message: 'Recording did not start: the microphone is in use or could not be opened.',
        hint: 'Close other apps that use the microphone, then try again.',
        details: detail,
        cause: error,
      });
    default:
      return new AppError({
        code: 'MIC_ERROR',
        message: 'Recording did not start: the microphone could not be opened.',
        hint: 'Try again; if it repeats, reload the page.',
        details: detail,
        cause: error,
      });
  }
}

export interface RecorderCallbacks {
  /** Input level 0..1, about 20 times a second while recording. */
  readonly onLevel: (level: number) => void;
  readonly onStop: (recording: Recording) => void;
  readonly onError: (error: AppError) => void;
}

/** One recording session: start once, stop once, then discard. */
export class RecorderSession {
  readonly #callbacks: RecorderCallbacks;
  #stream: MediaStream | null = null;
  #recorder: MediaRecorder | null = null;
  #audio: AudioContext | null = null;
  #levelTimer: number | null = null;
  #chunks: Blob[] = [];
  #startedAt = 0;
  #mimeType = '';
  #disposed = false;

  constructor(callbacks: RecorderCallbacks) {
    this.#callbacks = callbacks;
  }

  #isDisposed(): boolean {
    return this.#disposed;
  }

  get startedAt(): number {
    return this.#startedAt;
  }

  async start(): Promise<void> {
    assert(this.#recorder === null && !this.#disposed, 'a recorder session starts once');
    const unsupported = recordingUnsupported();
    if (unsupported !== null) throw unsupported;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch (error) {
      throw microphoneError(error);
    }
    // dispose() may have run while the permission prompt was open.
    if (this.#isDisposed()) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    this.#stream = stream;
    const mimeType = pickMimeType();
    let recorder: MediaRecorder;
    try {
      recorder =
        mimeType === null
          ? new MediaRecorder(stream)
          : new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 32_000 });
    } catch (error) {
      this.dispose();
      throw new AppError({
        code: 'RECORDING_FAILED',
        message: 'Recording did not start: the browser rejected the audio format.',
        hint: 'Upload an audio file instead, or try another browser.',
        details: [
          { label: 'Format', value: mimeType ?? '(browser default)' },
          { label: 'Cause', value: error instanceof Error ? error.message : String(error) },
        ],
        cause: error,
      });
    }
    this.#recorder = recorder;
    this.#mimeType = recorder.mimeType !== '' ? recorder.mimeType : (mimeType ?? 'audio/webm');
    recorder.addEventListener('dataavailable', (event) => {
      if (event.data.size > 0) this.#chunks.push(event.data);
    });
    recorder.addEventListener('stop', () => {
      this.#finish();
    });
    recorder.addEventListener('error', (event) => {
      const cause = (event as Event & { error?: unknown }).error;
      this.#callbacks.onError(
        new AppError({
          code: 'RECORDING_FAILED',
          message: 'Recording stopped: the browser reported a recording error.',
          hint: 'Record again. Audio captured so far is lost.',
          details: [{ label: 'Cause', value: cause instanceof Error ? cause.message : 'unknown' }],
          cause,
        }),
      );
      this.dispose();
    });
    this.#startMeter(stream);
    recorder.start(1000);
    this.#startedAt = performance.now();
  }

  #startMeter(stream: MediaStream): void {
    if (typeof AudioContext === 'undefined') return;
    const audio = new AudioContext();
    const analyser = audio.createAnalyser();
    analyser.fftSize = 512;
    audio.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    this.#audio = audio;
    this.#levelTimer = window.setInterval(() => {
      analyser.getByteTimeDomainData(samples);
      let peak = 0;
      for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128));
      this.#callbacks.onLevel(Math.min(1, peak / 100));
    }, 50);
  }

  stop(): void {
    if (this.#recorder !== null && this.#recorder.state !== 'inactive') this.#recorder.stop();
  }

  #finish(): void {
    const durationMs = performance.now() - this.#startedAt;
    const mimeType = bareMimeType(this.#mimeType);
    const blob = new Blob(this.#chunks, { type: mimeType });
    this.#releaseDevices();
    if (this.#disposed) return;
    if (blob.size === 0) {
      this.#callbacks.onError(
        new AppError({
          code: 'RECORDING_EMPTY',
          message: 'The recording is empty: no audio data arrived from the microphone.',
          hint: 'Check the input level while recording, then record again.',
        }),
      );
      return;
    }
    this.#callbacks.onStop({ blob, mimeType, extension: extensionFor(mimeType), durationMs });
  }

  #releaseDevices(): void {
    if (this.#levelTimer !== null) window.clearInterval(this.#levelTimer);
    this.#levelTimer = null;
    if (this.#audio !== null) {
      // Closing only frees audio resources; a failure leaves nothing to undo.
      void this.#audio.close().catch(() => undefined);
    }
    this.#audio = null;
    if (this.#stream !== null) for (const track of this.#stream.getTracks()) track.stop();
    this.#stream = null;
  }

  /** Stops everything without producing a recording. */
  dispose(): void {
    this.#disposed = true;
    if (this.#recorder !== null && this.#recorder.state !== 'inactive') {
      this.#chunks = [];
      this.#recorder.stop();
    }
    this.#releaseDevices();
  }
}
