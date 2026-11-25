/**
 * R25FIX-FU-1: the directory picker fits the container it is put in. In the agent builder's 340 px card its field
 * (then `min-width: 260px`) and Search button, on one line that could not wrap, ran past the card's right edge.
 *
 * - R25UI-DIR-1: the field and its button share a wrapping row and the field has no fixed minimum width.
 * - R25UI-DIR-2: a long name and email wrap inside the result's button instead of widening it.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import { DirectoryPicker } from '../directory-picker';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

function show() {
  render(
    <Theme>
      <DirectoryPicker
        label="Check for someone else"
        search={async () => [{ id: 'u1', name: 'Alexandria Montgomery-Fitzgerald', detail: 'alexandria.montgomery-fitzgerald@example.com' }]}
        exclude={[]}
        onPick={() => undefined}
      />
    </Theme>,
  );
}

describe('DirectoryPicker fits its container', () => {
  it('R25UI-DIR-1: the field and its button wrap; the field has no fixed minimum width', () => {
    show();
    const field = screen.getByRole('textbox', { name: 'Check for someone else' });
    const root = field.closest('.rt-TextFieldRoot') as HTMLElement;
    expect(['0', '0px']).toContain(root.style.minWidth);
    expect(root.style.flex).toBe('1 1 16rem');
    const row = root.parentElement as HTMLElement;
    expect(row.className).toMatch(/rt-r-fw-wrap/);
    expect(row.contains(screen.getByRole('button', { name: 'Search' }))).toBe(true);
  });

  it('R25UI-DIR-2: a long result wraps inside its button', async () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    const option = await screen.findByRole('listitem', { name: /Alexandria Montgomery-Fitzgerald/ });
    expect(option.style.maxWidth).toBe('100%');
    expect(option.style.whiteSpace).toBe('normal');
    expect(option.style.overflowWrap).toBe('anywhere');
  });
});
