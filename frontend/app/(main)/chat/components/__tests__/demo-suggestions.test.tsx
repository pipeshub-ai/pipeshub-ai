import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { useRestrictedQuestionAccess } from '@/app/(main)/workspace/connectors/demo-data/use-restricted-question';
import { DemoSuggestions } from '../demo-suggestions';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { returnObjects?: boolean; email?: string }) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      if (opts?.returnObjects) return cur;
      return typeof cur === 'string' ? cur.replace('{{email}}', opts?.email ?? '') : key;
    },
  }),
}));

vi.mock('@/app/(main)/workspace/connectors/demo-data/use-restricted-question', () => ({
  useRestrictedQuestionAccess: vi.fn(),
}));

const hideDemo = vi.fn();
vi.mock('@/app/(main)/workspace/connectors/demo-data/use-demo-switch', () => ({
  useDemoSwitch: () => ({ hideWithUndo: hideDemo, busy: false }),
}));

const access = vi.mocked(useRestrictedQuestionAccess);
const questions = Object.values(en.chat.demoSuggestions).map((q) => q.text);
const pricing = en.chat.demoSuggestions['5'].text;

function renderSuggestions(onPick = vi.fn(), isAdmin = true) {
  render(
    <Theme>
      <DemoSuggestions isAdmin={isAdmin} isMobile={false} onPick={onPick} />
    </Theme>,
  );
  return onPick;
}

beforeEach(() => {
  access.mockReset();
});

afterEach(() => cleanup());

describe('DemoSuggestions', () => {
  it('offers every golden question', () => {
    access.mockReturnValue(null);
    renderSuggestions();

    for (const q of questions) expect(screen.getByText(q)).toBeTruthy();
  });

  it('tells someone without access, before they ask, and who to sign in as instead', () => {
    access.mockReturnValue({ canSee: false, readerEmail: 'bob@acme-demo.example' });
    renderSuggestions();

    const hint = en.chat.demoRestrictedHintSignIn.replace('{{email}}', 'bob@acme-demo.example');
    const caption = screen.getByText('bob@acme-demo.example').parentElement;
    expect(caption?.textContent).toBe(hint);
    expect(screen.getByText('bob@acme-demo.example').style.whiteSpace).toBe('nowrap');
    // One lock, on the pricing question's chip.
    expect(screen.getAllByText('lock')).toHaveLength(1);
    expect(screen.getByText(pricing).closest('button')?.textContent).toContain('lock');
  });

  it('does not send a member to an account whose password only the admin knows', () => {
    access.mockReturnValue({ canSee: false, readerEmail: 'bob@acme-demo.example' });
    renderSuggestions(vi.fn(), false);

    expect(screen.getByText(en.chat.demoRestrictedHint)).toBeTruthy();
    expect(screen.queryByText(/Sign in as/)).toBeNull();
  });

  it('does not name an account to sign in as when there is none', () => {
    access.mockReturnValue({ canSee: false, readerEmail: null });
    renderSuggestions();

    expect(screen.getByText(en.chat.demoRestrictedHint)).toBeTruthy();
    expect(screen.queryByText(/Sign in as/)).toBeNull();
  });

  it('shows a plain question to the pricing committee, and while access is unknown', () => {
    for (const state of [{ canSee: true, readerEmail: null }, null]) {
      access.mockReturnValue(state);
      renderSuggestions();

      expect(screen.queryByText('lock')).toBeNull();
      expect(screen.queryByText(/Pricing committee only/)).toBeNull();
      cleanup();
    }
  });

  it('asks the question that was clicked, restricted or not', () => {
    access.mockReturnValue({ canSee: false, readerEmail: null });
    const onPick = renderSuggestions();

    fireEvent.click(screen.getByText(pricing));

    expect(onPick).toHaveBeenCalledWith({ id: '5', text: pricing, icons: en.chat.demoSuggestions['5'].icons });
  });

  it('lets the person hide the demo from the landing itself', () => {
    access.mockReturnValue(null);
    renderSuggestions();

    fireEvent.click(screen.getByRole('button', { name: en.chat.demoHide }));

    expect(hideDemo).toHaveBeenCalledTimes(1);
  });
});
