import React, { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

import { TagInput, type TagItem } from '../tag-input';
import { validateEmail } from '../../users/invite-email';

afterEach(() => cleanup());

function Harness(props: {
  freeText?: boolean;
  warnings?: Record<string, string>;
  warningsSummary?: string;
  initial?: TagItem[];
}) {
  const [tags, setTags] = useState<TagItem[]>(props.initial ?? []);
  return (
    <Theme>
      <TagInput
        tags={tags}
        onTagsChange={setTags}
        validate={props.freeText ? undefined : validateEmail}
        freeText={props.freeText}
        warnings={props.warnings}
        warningsSummary={props.warningsSummary}
        placeholder="Add"
      />
    </Theme>
  );
}

const input = () => screen.getByPlaceholderText('Add') as HTMLInputElement;
const pillTexts = () =>
  Array.from(document.querySelectorAll('[title]')).map((el) => el.getAttribute('title'));

describe('TagInput blur', () => {
  it('drops a trailing comma instead of keeping it inside an invalid tag', () => {
    render(<Harness />);
    // Text that arrives without key presses (a fill, an IME) keeps its comma until blur.
    fireEvent.change(input(), { target: { value: 'parity-test@example.com,' } });
    fireEvent.blur(input());
    expect(pillTexts()).toEqual(['parity-test@example.com']);
    expect(screen.queryByText(/invalid/i)).toBeNull();
  });

  it('splits on white space, commas and semicolons at blur', () => {
    render(<Harness />);
    fireEvent.change(input(), { target: { value: 'a@b.co; c@d.io,e@f.org  g@h.net' } });
    fireEvent.blur(input());
    expect(pillTexts()).toEqual(['a@b.co', 'c@d.io', 'e@f.org', 'g@h.net']);
  });

  it('splits a paste on semicolons as well', () => {
    render(<Harness />);
    fireEvent.paste(input(), { clipboardData: { getData: () => 'a@b.co;c@d.io' } });
    expect(pillTexts()).toEqual(['a@b.co', 'c@d.io']);
  });

  it('does not add an address twice, whatever its letter case', () => {
    render(<Harness />);
    fireEvent.change(input(), { target: { value: 'a@b.co, A@B.CO' } });
    fireEvent.blur(input());
    expect(pillTexts()).toEqual(['a@b.co']);
  });

  it('keeps commas inside a free-text value', () => {
    render(<Harness freeText />);
    fireEvent.change(input(), { target: { value: '/data/a, b;c' } });
    fireEvent.blur(input());
    expect(pillTexts()).toEqual(['/data/a, b;c']);
  });
});

describe('TagInput warnings', () => {
  const tags: TagItem[] = [
    { id: '1', value: 'dave@lab.test', isValid: true },
    { id: '2', value: 'new@lab.test', isValid: true },
  ];

  it('shows a noted address in amber with the note under the field', () => {
    render(<Harness initial={tags} warnings={{ 'dave@lab.test': 'dave@lab.test is already a member.' }} />);
    expect(screen.getByRole('status').textContent).toBe('dave@lab.test is already a member.');
    const pill = screen.getByText('dave@lab.test').parentElement as HTMLElement;
    expect(pill.style.backgroundColor).toBe('var(--amber-a3)');
    expect(pill.style.border).toContain('var(--amber-a7)');
    const other = screen.getByText('new@lab.test').parentElement as HTMLElement;
    expect(other.style.backgroundColor).toBe('var(--accent-a3)');
  });

  it('shows one summary line when several addresses have notes', () => {
    render(
      <Harness
        initial={tags}
        warnings={{ 'dave@lab.test': 'one', 'new@lab.test': 'two' }}
        warningsSummary="Addresses in amber are already here."
      />,
    );
    expect(screen.getByRole('status').textContent).toBe('Addresses in amber are already here.');
  });

  it('leaves an invalid address red, with no note', () => {
    render(
      <Harness
        initial={[{ id: '1', value: 'dave@', isValid: false }]}
        warnings={{ 'dave@': 'should not show' }}
      />,
    );
    expect(screen.queryByRole('status')).toBeNull();
  });
});
