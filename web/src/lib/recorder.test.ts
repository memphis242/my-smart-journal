import { describe, expect, it } from 'vitest';
import { uploadAudioType } from './recorder';

describe('uploadAudioType', () => {
  it('keeps types the server accepts and drops codec parameters', () => {
    expect(uploadAudioType('note.webm', 'audio/webm;codecs=opus')).toBe('audio/webm');
    expect(uploadAudioType('note.wav', 'audio/wav')).toBe('audio/wav');
  });

  it('maps the types browsers give common audio files', () => {
    expect(uploadAudioType('tone.webm', 'video/webm')).toBe('audio/webm');
    expect(uploadAudioType('memo.m4a', 'audio/x-m4a')).toBe('audio/mp4');
  });

  it('falls back to the extension when the type is missing or generic', () => {
    expect(uploadAudioType('voice.opus', '')).toBe('audio/ogg');
    expect(uploadAudioType('voice.FLAC', 'application/octet-stream')).toBe('audio/flac');
  });

  it('rejects what is not audio, whatever the file is called', () => {
    expect(uploadAudioType('notes.txt', 'text/plain')).toBeNull();
    expect(uploadAudioType('photo', 'image/png')).toBeNull();
    expect(uploadAudioType('clip.webm', 'text/plain')).toBeNull();
  });
});
