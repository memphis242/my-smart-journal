import type { EntrySummary, RetrieveResult, SearchHit, SearchTier, Unavailable } from '../api/types';
import type { SeedConcept } from './seed-types';

export interface SearchDocument {
  readonly entry: EntrySummary;
  readonly chunks: readonly string[];
  readonly embedded: boolean;
}

const TIER_NAMES = ['exact', 'substring', 'fuzzy', 'deterministic', 'semantic'] as const;
const WORD_PATTERN = /[\p{L}\p{N}]+/gu;

function words(text: string): readonly string[] {
  return text.toLocaleLowerCase().match(WORD_PATTERN) ?? [];
}

function haystack(document: SearchDocument): string {
  return [document.entry.title ?? '', document.entry.text, document.entry.summary ?? '', ...document.entry.tags].join('\n');
}

function snippet(text: string, phrase: string): string {
  const index = text.toLocaleLowerCase().indexOf(phrase.toLocaleLowerCase());
  if (text.length <= 160) return text;
  const start = Math.max(0, (index < 0 ? 0 : index) - 60);
  const end = Math.min(text.length, start + 154);
  return `${start > 0 ? '...' : ''}${text.slice(start, end)}${end < text.length ? '...' : ''}`;
}

function editDistance(left: string, right: string): number {
  const previous = Array.from({ length: right.length + 1 }, (_unused, index) => index);
  for (let row = 1; row <= left.length; row += 1) {
    let diagonal = previous[0] ?? 0;
    previous[0] = row;
    for (let column = 1; column <= right.length; column += 1) {
      const above = previous[column] ?? 0;
      const cost = left[row - 1] === right[column - 1] ? 0 : 1;
      previous[column] = Math.min((previous[column - 1] ?? 0) + 1, above + 1, diagonal + cost);
      diagonal = above;
    }
  }
  return previous[right.length] ?? Math.max(left.length, right.length);
}

function fuzzyScore(queryWords: readonly string[], contentWords: readonly string[]): number {
  if (queryWords.length === 0 || contentWords.length === 0) return 0;
  const total = queryWords.reduce((sum, queryWord) => {
    const best = contentWords.reduce((maximum, contentWord) => {
      if (contentWord.includes(queryWord) || queryWord.includes(contentWord)) {
        return Math.max(maximum, Math.min(contentWord.length, queryWord.length) / Math.max(contentWord.length, queryWord.length));
      }
      return Math.max(maximum, 1 - editDistance(queryWord, contentWord) / Math.max(queryWord.length, contentWord.length));
    }, 0);
    return sum + best;
  }, 0);
  return total / queryWords.length;
}

function deterministicScore(document: SearchDocument, queryWords: readonly string[], now: number): number {
  const titleWords = words(document.entry.title ?? '');
  const textWords = words(document.entry.text);
  const summaryWords = words(document.entry.summary ?? '');
  const tagWords = words(document.entry.tags.join(' '));
  const matchCount = queryWords.filter((word) =>
    titleWords.includes(word) || textWords.includes(word) || summaryWords.includes(word) || tagWords.includes(word),
  ).length;
  if (matchCount === 0) return 0;
  const weighted = queryWords.reduce((sum, word) =>
    sum + Number(titleWords.includes(word)) * 3 + Number(summaryWords.includes(word)) * 2 + Number(tagWords.includes(word)) * 2 + Number(textWords.includes(word)), 0);
  const ageDays = Math.max(0, (now - Date.parse(`${document.entry.entry_date}T00:00:00Z`)) / 86_400_000);
  const recency = 1 + Math.max(0, 30 - ageDays) / 300;
  return (weighted * recency) / Math.max(1, queryWords.length);
}

function conceptWords(queryWords: readonly string[], concepts: readonly SeedConcept[]): ReadonlySet<string> {
  const expanded = new Set(queryWords);
  for (const concept of concepts) {
    const group = [concept.name, ...concept.words].flatMap(words);
    const searchable = [concept.name, ...concept.words].map((value) => value.toLocaleLowerCase());
    if (queryWords.some((queryWord) => searchable.some((value) => value.includes(queryWord)))) {
      for (const word of group) expanded.add(word);
    }
  }
  return expanded;
}

function semanticScore(chunks: readonly string[], expanded: ReadonlySet<string>): { readonly score: number; readonly passage: string } {
  let best = { score: 0, passage: '' };
  for (const passage of chunks) {
    const contentWords = new Set(words(passage));
    if (contentWords.size === 0 || expanded.size === 0) continue;
    let overlap = 0;
    for (const word of expanded) if (contentWords.has(word)) overlap += 1;
    const score = overlap === 0 ? 0 : Math.min(0.9, 0.35 + (overlap / Math.max(1, Math.min(expanded.size, 5))) * 0.55);
    if (score > best.score) best = { score, passage };
  }
  return best;
}

function hit(document: SearchDocument, score: number, matchText: string, phrase: string, passage: string | null = null): SearchHit {
  return { entry_id: document.entry.id, score, snippet: snippet(matchText, phrase), passage, entry: null };
}

function ranked<T>(items: readonly T[], score: (item: T) => number): readonly T[] {
  return items.filter((item) => score(item) > 0).toSorted((left, right) => score(right) - score(left));
}

export function tierResults(
  documents: readonly SearchDocument[],
  query: string,
  concepts: readonly SeedConcept[],
  requestedTiers: readonly SearchTier[] = [1, 2, 3, 4, 5],
  now = Date.now(),
): readonly (readonly SearchHit[])[] {
  const phrase = query.trim();
  const queryWords = words(phrase);
  const used = new Set<string>();
  const tiers: SearchHit[][] = [[], [], [], [], []];
  const assign = (tierIndex: number, matching: readonly { readonly document: SearchDocument; readonly score: number; readonly source: string; readonly passage?: string }[]): void => {
    const unique = matching.filter(({ document }) => !used.has(document.entry.id));
    for (const item of unique) used.add(item.document.entry.id);
    tiers[tierIndex] = unique.map(({ document, score, source, passage: bestPassage }) =>
      hit(document, score, source, phrase, bestPassage ?? null));
  };

  for (const tier of requestedTiers) {
    switch (tier) {
      case 1:
        assign(0, documents.filter((document) => haystack(document).includes(phrase))
          .map((document) => ({ document, score: 1, source: haystack(document) })));
        break;
      case 2:
        assign(1, documents.filter((document) => haystack(document).toLocaleLowerCase().includes(phrase.toLocaleLowerCase()))
          .map((document) => ({ document, score: 0.95, source: haystack(document) })));
        break;
      case 3:
        assign(2, documents.map((document) => ({ document, score: fuzzyScore(queryWords, words(haystack(document))), source: haystack(document) }))
          .filter((item) => item.score >= 0.75).toSorted((left, right) => right.score - left.score));
        break;
      case 4: {
        const scores = documents.map((document) => ({ document, score: deterministicScore(document, queryWords, now), source: haystack(document) }));
        const maximum = Math.max(0, ...scores.map((item) => item.score));
        assign(3, scores.filter((item) => item.score > 0)
          .map((item) => ({ ...item, score: maximum === 0 ? 0 : item.score / maximum }))
          .toSorted((left, right) => right.score - left.score));
        break;
      }
      case 5: {
        const expanded = conceptWords(queryWords, concepts);
        const semantic = documents.filter((document) => !used.has(document.entry.id))
          .map((document) => ({ document, ...semanticScore(document.chunks, expanded) }))
          .filter((item) => item.score >= 0.35)
          .toSorted((left, right) => right.score - left.score)
          .slice(0, 20)
          .map(({ document, score, passage }) => ({ document, score, source: passage, passage }));
        assign(4, semantic);
        break;
      }
    }
  }
  return TIER_NAMES.map((_name, index) => tiers[index] ?? []);
}

export function semanticUnavailable(
  documents: readonly SearchDocument[],
  embeddings: 'ok' | 'disabled' | 'model_unavailable',
): Unavailable | null {
  if (embeddings === 'disabled') {
    return { code: 'EMBEDDINGS_DISABLED', detail: 'Semantic search is off: [embedding] provider is "none".' };
  }
  if (embeddings === 'model_unavailable') {
    return { code: 'EMBEDDING_MODEL_UNAVAILABLE', detail: 'The embedding model BAAI/bge-small-en-v1.5 failed to load. Check [embedding] model and the service log.' };
  }
  const pending = documents.filter((document) => !document.embedded).length;
  return pending === 0 ? null : {
    code: 'EMBEDDINGS_PENDING',
    detail: `${pending} entries are not embedded yet; results may miss them.`,
  };
}

export function retrieve(
  documents: readonly SearchDocument[],
  query: string,
  concepts: readonly SeedConcept[],
  embeddings: 'ok' | 'disabled' | 'model_unavailable',
  k: number,
  now = Date.now(),
): RetrieveResult {
  const queryWords = words(query);
  const semanticQuery = conceptWords(queryWords, concepts);
  const bm25 = ranked(documents, (document) => deterministicScore(document, queryWords, now));
  const semantic = embeddings === 'ok'
    ? documents.filter((document) => semanticScore(document.chunks, semanticQuery).score >= 0.35)
      .toSorted((left, right) => semanticScore(right.chunks, semanticQuery).score - semanticScore(left.chunks, semanticQuery).score).slice(0, 20)
    : [];
  const rrf = new Map<string, number>();
  for (const list of [bm25, semantic]) {
    list.forEach((document, index) => {
      rrf.set(document.entry.id, (rrf.get(document.entry.id) ?? 0) + 1 / (60 + index + 1));
    });
  }
  const maximum = Math.max(0, ...rrf.values());
  const documentsById = new Map(documents.map((document) => [document.entry.id, document]));
  const items = [...rrf.entries()]
    .toSorted((left, right) => right[1] - left[1])
    .slice(0, k)
    .flatMap(([id, score]) => {
      const document = documentsById.get(id);
      if (document === undefined) return [];
      const best = semanticScore(document.chunks, semanticQuery);
      const passageList = document.chunks
        .map((passage) => ({ passage, score: words(passage).reduce((sum, word) => sum + Number(semanticQuery.has(word)), 0) }))
        .filter((item) => item.score > 0)
        .toSorted((left, right) => right.score - left.score)
        .slice(0, 3)
        .map((item) => item.passage);
      if (passageList.length === 0) passageList.push(...document.chunks.slice(0, 3));
      if (passageList.length === 0 && best.passage !== '') passageList.push(best.passage);
      if (passageList.length === 0) passageList.push([document.entry.text, document.entry.summary, document.entry.title].find((value) => value !== null && value !== '') ?? '');
      return [{ entry: document.entry, score: maximum === 0 ? 0 : Number((score / maximum).toFixed(2)), passages: passageList }];
    });
  return { items, unavailable: semanticUnavailable(documents, embeddings) };
}
