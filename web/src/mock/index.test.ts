import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { installMock } from './index';

describe('browser mock installation options', () => {
  it('uses URL options and persists signed-in state in sessionStorage', async () => {
    const values = new Map<string, string>();
    vi.stubGlobal('window', {
      location: {
        search: '?mock-latency=0&mock-job-ms=0&mock-pickup-ms=0&mock-search-ms=0&mock-worker=stale&mock-batch-error=1&mock-token=rotated&mock-embeddings=disabled&mock-signed-in=1&mock-empty=1',
      },
      sessionStorage: {
        getItem: (key: string): string | null => values.get(key) ?? null,
        setItem: (key: string, value: string): void => { values.set(key, value); },
      },
    });
    try {
      const fetch = installMock();
      const client = new ApiClient({ fetch, onUnauthorized: () => undefined });
      expect(values.get('msj.mock.session')).toBe('1');
      await expect(client.sessionProblem()).resolves.toMatchObject({ code: 'TOKEN_REISSUE_REQUIRED' });
      await client.signIn('current-token');
      await expect(client.sessionProblem()).resolves.toBeNull();
      const page = await client.listEntries({ filters: {}, order: 'desc', limit: 100, offset: 0 });
      expect(page).toMatchObject({ items: [], total: 0 });
      expect(values.get('msj.mock.session')).toBe('1');
      await expect(client.queue()).resolves.toMatchObject({
        worker: { next_run_at: null },
        batch: { last_result: { processed: 5, failed: 0 }, last_error: { code: 'BATCH_FAILED' } },
      });
      const events: { code: string | null; results: number }[] = [];
      await client.search({ q: 'semantic option', filters: {}, tiers: [5] }, (event) => {
        if (event.type === 'tier') {
          events.push({ code: event.unavailable?.code ?? null, results: event.results.length });
        }
      });
      expect(events).toEqual([{ code: 'EMBEDDINGS_DISABLED', results: 0 }]);
      await client.signOut();
      expect(values.get('msj.mock.session')).toBe('0');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('rejects malformed duration options with the option name and value', () => {
    vi.stubGlobal('window', {
      location: { search: '?mock-pickup-ms=-4' },
      sessionStorage: { getItem: (): string | null => null, setItem: (): void => undefined },
    });
    try {
      expect(() => installMock()).toThrow(/mock-pickup-ms="-4"/);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
