/** Record in the browser, or pick an audio file; shows the take with its length and size before upload. */

import type { JSX } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { AppError } from '../api/errors';
import { formatBytes, formatClock } from '../lib/format';
import { RecorderSession, bareMimeType, extensionFor, recordingUnsupported, type Recording } from '../lib/recorder';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';

/** Upload types the server accepts (backend `_AUDIO_TYPES`). */
const ACCEPTED = ['audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp4', 'audio/webm', 'audio/ogg', 'audio/flac', 'audio/x-flac'] as const;
const FILE_EXTENSIONS: Readonly<Record<string, string>> = {
  'audio/wav': 'wav',
  'audio/x-wav': 'wav',
  'audio/mpeg': 'mp3',
  'audio/mp4': 'm4a',
  'audio/webm': 'webm',
  'audio/ogg': 'ogg',
  'audio/flac': 'flac',
  'audio/x-flac': 'flac',
};
const METER_SEGMENTS = 14;

export interface AudioTake {
  readonly blob: Blob;
  readonly mimeType: string;
  readonly fileName: string;
  readonly durationMs: number | null;
}

type RecorderView =
  | { readonly kind: 'idle' }
  | { readonly kind: 'starting' }
  | { readonly kind: 'recording'; readonly startedAt: number }
  | { readonly kind: 'ready'; readonly take: AudioTake };

export interface AudioRecorderProps {
  readonly onTake: (take: AudioTake | null) => void;
  readonly onRecordingChange: (recording: boolean) => void;
}

function takeFromRecording(recording: Recording): AudioTake {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '');
  return { blob: recording.blob, mimeType: recording.mimeType, fileName: `recording-${stamp}.${recording.extension}`, durationMs: recording.durationMs };
}

export function AudioRecorder({ onTake, onRecordingChange }: AudioRecorderProps): JSX.Element {
  const [state, setState] = useState<RecorderView>({ kind: 'idle' });
  const [error, setError] = useState<AppError | null>(null);
  const [level, setLevel] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const session = useRef<RecorderSession | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const unsupported = useMemo(() => recordingUnsupported(), []);

  const previewUrl = useMemo(() => (state.kind === 'ready' ? URL.createObjectURL(state.take.blob) : null), [state]);
  useEffect(
    () => () => {
      if (previewUrl !== null) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );

  useEffect(
    () => () => {
      session.current?.dispose();
    },
    [],
  );

  useEffect(() => {
    if (state.kind !== 'recording') return undefined;
    const timer = window.setInterval(() => {
      setElapsed(performance.now() - state.startedAt);
    }, 250);
    return () => {
      window.clearInterval(timer);
    };
  }, [state]);

  const start = async (): Promise<void> => {
    setError(null);
    onTake(null);
    setState({ kind: 'starting' });
    const recorder = new RecorderSession({
      onLevel: setLevel,
      onStop: (recording) => {
        const take = takeFromRecording(recording);
        setState({ kind: 'ready', take });
        onTake(take);
        onRecordingChange(false);
        setLevel(0);
      },
      onError: (caught) => {
        setError(caught);
        setState({ kind: 'idle' });
        onRecordingChange(false);
        setLevel(0);
      },
    });
    session.current = recorder;
    try {
      await recorder.start();
      setElapsed(0);
      setState({ kind: 'recording', startedAt: recorder.startedAt });
      onRecordingChange(true);
    } catch (caught) {
      setError(caught instanceof AppError ? caught : new AppError({ code: 'MIC_ERROR', message: 'Recording did not start.', cause: caught }));
      setState({ kind: 'idle' });
    }
  };

  const stop = (): void => {
    session.current?.stop();
  };

  const discard = (): void => {
    onTake(null);
    setState({ kind: 'idle' });
  };

  const pickFile = (file: File): void => {
    setError(null);
    const type = bareMimeType(file.type);
    if (!(ACCEPTED as readonly string[]).includes(type)) {
      setError(
        new AppError({
          code: 'UNSUPPORTED_AUDIO_TYPE',
          message: `"${file.name}" cannot be uploaded: its type ${file.type === '' ? '(unknown)' : file.type} is not accepted.`,
          hint: 'Use WAV, MP3, M4A (MP4), WebM, Ogg or FLAC audio.',
        }),
      );
      return;
    }
    const extension = FILE_EXTENSIONS[type] ?? extensionFor(type);
    const name = /\.[a-z0-9]+$/i.test(file.name) ? file.name : `${file.name}.${extension}`;
    const take: AudioTake = { blob: file, mimeType: type, fileName: name, durationMs: null };
    setState({ kind: 'ready', take });
    onTake(take);
  };

  const litSegments = Math.round(level * METER_SEGMENTS);

  return (
    <div class="recorder" data-testid="recorder">
      {state.kind === 'recording' ? (
        <div class="recorder-live">
          <span class="recording-dot" aria-hidden="true" />
          <span class="mono recorder-clock" data-testid="recorder-clock">
            {formatClock(elapsed)}
          </span>
          <span class="level-meter" title="Input level" aria-label="Input level" role="meter" aria-valuemin={0} aria-valuemax={METER_SEGMENTS} aria-valuenow={litSegments}>
            {Array.from({ length: METER_SEGMENTS }, (_, index) => (
              <span class={index < litSegments ? 'level-seg is-lit' : 'level-seg'} key={index} />
            ))}
          </span>
          <button type="button" class="button button-primary" onClick={stop} data-testid="recorder-stop">
            <Icon name="stop" size={16} />
            <span>Stop</span>
          </button>
        </div>
      ) : state.kind === 'ready' ? (
        <div class="recorder-take">
          {previewUrl !== null && <audio class="take-audio" controls src={previewUrl} preload="metadata" />}
          <p class="take-meta mono">
            {state.take.durationMs !== null && <span>{formatClock(state.take.durationMs)}</span>}
            <span>{formatBytes(state.take.blob.size)}</span>
            <span>{state.take.mimeType}</span>
            <span class="take-name" title={state.take.fileName}>
              {state.take.fileName}
            </span>
          </p>
          <div class="recorder-actions">
            <button type="button" class="button button-small" onClick={discard}>
              Discard
            </button>
            {unsupported === null && (
              <button type="button" class="button button-small" onClick={() => void start()}>
                <Icon name="mic" size={14} />
                <span>Record again</span>
              </button>
            )}
          </div>
        </div>
      ) : (
        <div class="recorder-idle">
          <button
            type="button"
            class="button button-record"
            disabled={unsupported !== null || state.kind === 'starting'}
            title={unsupported === null ? 'Start recording from the microphone' : unsupported.message}
            data-testid="recorder-start"
            onClick={() => void start()}
          >
            <Icon name="mic" size={18} />
            <span>{state.kind === 'starting' ? 'Waiting for the microphone' : 'Start recording'}</span>
          </button>
          <span class="recorder-or">
            <span class="muted">or</span>
            <button
              type="button"
              class="button"
              title="Upload an existing recording: WAV, MP3, M4A, WebM, Ogg or FLAC"
              onClick={() => {
                fileInput.current?.click();
              }}
            >
              <Icon name="upload" size={16} />
              <span>Choose a file</span>
            </button>
          </span>
          <input
            ref={fileInput}
            class="visually-hidden"
            type="file"
            accept={ACCEPTED.join(',')}
            tabIndex={-1}
            data-testid="audio-file"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              if (file !== undefined) pickFile(file);
              event.currentTarget.value = '';
            }}
          />
        </div>
      )}
      {unsupported !== null && state.kind === 'idle' && <ErrorNotice error={unsupported} compact />}
      {error !== null && <ErrorNotice error={error} compact onDismiss={() => { setError(null); }} />}
    </div>
  );
}
