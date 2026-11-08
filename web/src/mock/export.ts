import { assert } from '../lib/assert';
import { extraction } from '../api/guards';
import type { EntryDetail, ExportInclude, ExportRequest } from '../api/types';

function currentExtraction(entry: EntryDetail) {
  const artifact = entry.artifacts.find(
    (item) => item.kind === 'extraction' && item.revision === entry.revision,
  );
  if (artifact === undefined) return null;
  try {
    return extraction(JSON.parse(artifact.content) as unknown, 'extraction');
  } catch (error) {
    throw new Error(
      `Cannot export extraction artifact ${artifact.id} for entry ${entry.id}: its content is not valid extraction JSON. Repair the mock artifact before exporting.`,
      { cause: error },
    );
  }
}

function orderedEntries(entries: readonly EntryDetail[], order: ExportRequest['order']): readonly EntryDetail[] {
  const direction = order === 'asc' ? 1 : -1;
  return [...entries].sort((left, right) => {
    const dateOrder = left.entry_date.localeCompare(right.entry_date);
    if (dateOrder !== 0) return dateOrder * direction;
    const createdOrder = left.created_at.localeCompare(right.created_at);
    if (createdOrder !== 0) return createdOrder * direction;
    return left.id.localeCompare(right.id) * direction;
  });
}

function selectedFields(entry: EntryDetail, include: ExportInclude): Readonly<Record<string, unknown>> {
  const fields: Record<string, unknown> = { id: entry.id };
  if (include.date) fields['entry_date'] = entry.entry_date;
  if (include.title) fields['title'] = entry.title;
  if (include.tags) fields['tags'] = entry.tags;
  if (include.summary) fields['summary'] = entry.summary;
  if (include.text) fields['text'] = entry.text;
  if (include.extraction) fields['extraction'] = currentExtraction(entry);
  return fields;
}

function plainExtraction(entry: EntryDetail): readonly string[] {
  const value = currentExtraction(entry);
  if (value === null) return [];
  const sections: readonly (readonly [string, readonly string[]])[] = [
    ['Projects:', value.projects],
    ['Skills:', value.skills_technologies],
    ['Accomplishments:', value.accomplishments.map((item) =>
      item.metrics.length > 0 ? `${item.description} (${item.metrics.join(', ')})` : item.description,
    )],
    ['People:', value.people_roles.map((person) => `${person.name}: ${person.role}`)],
  ];
  return sections.flatMap(([label, items]) =>
    items.length === 0 ? [] : [label, ...items.map((item) => `    ${item}`), ''],
  ).slice(0, -1);
}

function textEntry(entry: EntryDetail, include: ExportInclude): string {
  const headerDate = include.date ? entry.entry_date : '';
  const headerTitle = include.title && entry.title !== '' ? entry.title : null;
  const header = headerTitle === null
    ? headerDate
    : headerDate === '' ? headerTitle : `${headerDate}  ${headerTitle}`;
  // As the backend's exporting._text_entry: labels on their own lines, tags only when there are some.
  const lines = header === '' ? [] : [header];
  if (include.summary && entry.summary !== null) lines.push('', 'Summary:', entry.summary);
  if (include.text && entry.text !== '') lines.push('', entry.text);
  if (include.tags && entry.tags.length > 0) lines.push('', 'Tags:', entry.tags.join(', '));
  if (include.extraction) {
    const extracted = plainExtraction(entry);
    if (extracted.length > 0) lines.push('', ...extracted);
  }
  return lines.join('\n');
}

function markdownEntry(entry: EntryDetail, include: ExportInclude): string {
  const headerDate = include.date ? entry.entry_date : '';
  const headerTitle = include.title && entry.title !== '' ? entry.title : null;
  // As the backend's exporting._markdown_entry.
  const heading = [headerDate, headerTitle].filter((part): part is string => part !== null && part !== '').join('  ');
  const sections = heading === '' ? [] : [`# ${heading}`];
  if (include.summary && entry.summary !== null) sections.push(`## Summary\n${entry.summary}`);
  if (include.text && entry.text !== '') sections.push(`## Text\n${entry.text}`);
  if (include.tags && entry.tags.length > 0) sections.push(`Tags: ${entry.tags.join(', ')}`);
  if (include.extraction) {
    const extracted = currentExtraction(entry);
    if (extracted !== null) {
      const groups: readonly (readonly [string, readonly string[]])[] = [
        ['Projects', extracted.projects],
        ['Skills', extracted.skills_technologies],
        ['Accomplishments', extracted.accomplishments.map((item) => item.description)],
        ['People', extracted.people_roles.map((person) => `${person.name}: ${person.role}`)],
      ];
      for (const [label, items] of groups) {
        if (items.length > 0) sections.push(`**${label}**\n\n${items.map((item) => `- ${item}`).join('\n')}`);
      }
    }
  }
  return sections.join('\n\n');
}

export function exportResponse(
  entries: readonly EntryDetail[],
  request: ExportRequest,
  today: string,
): Response {
  assert(entries.length === request.entry_ids.length, 'export entries match requested ids');
  const ordered = orderedEntries(entries, request.order);
  const extension = request.format === 'text' ? 'txt' : request.format === 'markdown' ? 'md' : 'json';
  const filename = `journal-export-${today}.${extension}`;
  let body: string;
  let contentType: string;
  switch (request.format) {
    case 'text':
      body = ordered.map((entry) => textEntry(entry, request.include)).join('\n\n--------------------\n\n');
      contentType = 'text/plain; charset=utf-8';
      break;
    case 'markdown':
      body = ordered.map((entry) => markdownEntry(entry, request.include)).join('\n\n---\n\n');
      contentType = 'text/markdown; charset=utf-8';
      break;
    case 'json':
      body = JSON.stringify(ordered.map((entry) => selectedFields(entry, request.include)), null, 2);
      contentType = 'application/json';
      break;
  }
  return new Response(body, {
    headers: {
      'Content-Type': contentType,
      'Content-Disposition': `attachment; filename="${filename}"`,
    },
  });
}
