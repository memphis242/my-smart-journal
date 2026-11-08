/** Tags as removable chips plus a text field; known tags are suggested as you type. */

import type { JSX } from 'preact';
import { useState } from 'preact/hooks';
import { Icon } from './Icon';

export interface TagInputProps {
  readonly id: string;
  readonly tags: readonly string[];
  readonly suggestions: readonly string[];
  readonly onChange: (tags: readonly string[]) => void;
}

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}

export function TagInput({ id, tags, suggestions, onChange }: TagInputProps): JSX.Element {
  const [draft, setDraft] = useState('');
  const [active, setActive] = useState(0);
  const lower = new Set(tags.map((tag) => tag.toLowerCase()));
  const prefix = draft.trim().toLowerCase();
  const matches =
    prefix === ''
      ? []
      : suggestions.filter((s) => s.toLowerCase().startsWith(prefix) && !lower.has(s.toLowerCase())).slice(0, 6);

  const add = (value: string): void => {
    const tag = normalize(value);
    setDraft('');
    setActive(0);
    if (tag === '' || lower.has(tag.toLowerCase())) return;
    onChange([...tags, tag]);
  };

  return (
    <div class="tag-input">
      <div class="tag-field">
        {tags.map((tag) => (
          <span class="chip chip-tag chip-removable" key={tag}>
            <span>{tag}</span>
            <button
              type="button"
              class="chip-remove"
              title={`Remove tag ${tag}`}
              aria-label={`Remove tag ${tag}`}
              onClick={() => {
                onChange(tags.filter((t) => t !== tag));
              }}
            >
              <Icon name="close" size={14} />
            </button>
          </span>
        ))}
        <input
          id={id}
          class="tag-entry"
          value={draft}
          autocomplete="off"
          placeholder={tags.length === 0 ? 'work, school, life' : ''}
          aria-autocomplete="list"
          data-testid="tag-input"
          onInput={(event) => {
            const value = event.currentTarget.value;
            if (value.includes(',')) {
              for (const part of value.split(',').slice(0, -1)) add(part);
              setDraft(value.split(',').at(-1) ?? '');
            } else {
              setDraft(value);
              setActive(0);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && draft.trim() !== '') {
              event.preventDefault();
              add(matches[active] ?? draft);
            } else if (event.key === 'Tab' && matches.length > 0 && draft.trim() !== '') {
              event.preventDefault();
              add(matches[active] ?? draft);
            } else if (event.key === 'ArrowDown' && matches.length > 0) {
              event.preventDefault();
              setActive((active + 1) % matches.length);
            } else if (event.key === 'ArrowUp' && matches.length > 0) {
              event.preventDefault();
              setActive((active - 1 + matches.length) % matches.length);
            } else if (event.key === 'Backspace' && draft === '' && tags.length > 0) {
              onChange(tags.slice(0, -1));
            }
          }}
          onBlur={() => {
            if (draft.trim() !== '') add(draft);
          }}
        />
      </div>
      {matches.length > 0 && (
        <ul class="tag-suggestions" role="listbox" aria-label="Known tags">
          {matches.map((match, index) => (
            <li
              key={match}
              role="option"
              aria-selected={index === active}
              class={index === active ? 'tag-suggestion is-active' : 'tag-suggestion'}
              onPointerDown={(event) => {
                event.preventDefault();
                add(match);
              }}
            >
              {match}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
