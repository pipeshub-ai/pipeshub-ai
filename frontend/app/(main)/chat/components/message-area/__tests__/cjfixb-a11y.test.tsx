/**
 * CJFIX-B accessibility: a button's name starts with the words on it (F5, WCAG 2.5.3 label in name), in every
 * locale; an answer's Answer / Sources / Citation are tabs with keyboard support, and the sidebar's Recents fold is a
 * button that says whether it is open (QA-CLASSIC-FU-1).
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import { ResponseTabs } from '../response-tabs';
import { RecentsToggle } from '../../../sidebar/recents-toggle';
import type { PendingApproval, ResponseTab } from '../../../types';

const h = React.createElement;
const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-TW', 'zh-SG', 'cs-CZ'];

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(async () => {
  cleanup();
  await i18n.changeLanguage('en-US');
});

const approval = (overrides: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: 'ix.approve.t1.0',
  toolCallId: 'call-1',
  toolName: 'e2esaas__create_ticket',
  displayName: 'Create ticket',
  args: '{"title":"Q3"}',
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  ...overrides,
});

const nameHoldsText = (button: HTMLElement) => {
  const name = (button.getAttribute('aria-label') ?? '').toLowerCase();
  const text = (button.textContent ?? '').trim().toLowerCase();
  return { name, text, ok: text.length > 0 && name.startsWith(text) };
};

describe('F5: approval buttons are named by their own words first', () => {
  it.each(LOCALES)('%s: Allow once, Allow for this conversation and Deny', async (locale) => {
    await i18n.changeLanguage(locale);
    const reason = { kind: 'external_send', destination: '#eng', conversationGrantable: true };
    render(h(Theme, null, h(ApprovalCard, { approval: approval({ reason } as Partial<PendingApproval>), onDecide: vi.fn() })));
    const buttons = screen.getAllByRole('button').filter((b) => b.getAttribute('aria-label'));
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      const { name, text, ok } = nameHoldsText(button);
      expect(ok, `"${name}" does not hold "${text}"`).toBe(true);
    }
  });

  it('en-US: Allow once names the tool after its words', () => {
    render(h(Theme, null, h(ApprovalCard, { approval: approval(), onDecide: vi.fn() })));
    expect(screen.getByRole('button', { name: /^Allow once: Create ticket/ })).toBeTruthy();
  });
});

describe('QA-CLASSIC-FU-1: the answer’s tabs', () => {
  function Tabs({ sources = 2, citations = 0 }: { sources?: number; citations?: number }) {
    const [tab, setTab] = React.useState<ResponseTab>('answer');
    return h(ResponseTabs, { activeTab: tab, onTabChange: setTab, sourcesCount: sources, citationCount: citations, idBase: 'a1' });
  }

  it('are a tab list with one selected tab and one tab stop', () => {
    render(h(Theme, null, h(Tabs)));
    expect(screen.getByRole('tablist', { name: 'Answer views' })).toBeTruthy();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    // One tab stop: the list (a roving-focus tab list), which hands the focus to the selected tab.
    const list = screen.getByRole('tablist', { name: 'Answer views' });
    expect([list, ...tabs].filter((el) => el.tabIndex === 0)).toEqual([list]);
    list.focus();
    expect(document.activeElement).toBe(tabs[0]);
    expect(tabs[0].getAttribute('aria-controls')).toBe('a1-panel');
    // A tab with nothing is disabled: read as unavailable, and passed over by the arrow keys.
    expect((tabs[2] as HTMLButtonElement).disabled).toBe(true);
  });

  it('arrow keys, Home and End move between the tabs that have something, and select them', async () => {
    render(h(Theme, null, h(Tabs)));
    const answer = screen.getByRole('tab', { name: 'Answer' });
    const sources = screen.getByRole('tab', { name: /^Sources/ });
    const selected = (tab: HTMLElement) => waitFor(() => expect(tab.getAttribute('aria-selected')).toBe('true'));
    answer.focus();
    // The tab list moves the focus just after the key, and a tab is selected as it takes the focus.
    fireEvent.keyDown(answer, { key: 'ArrowRight' });
    await selected(sources);
    expect(document.activeElement).toBe(sources);
    // Citations has none, so it is skipped: right from Sources wraps to Answer.
    fireEvent.keyDown(sources, { key: 'ArrowRight' });
    await selected(answer);
    fireEvent.keyDown(answer, { key: 'End' });
    await selected(sources);
    fireEvent.keyDown(sources, { key: 'Home' });
    await selected(answer);
  });

  it('a click on a tab with nothing does not select it', () => {
    render(h(Theme, null, h(Tabs)));
    fireEvent.click(screen.getByRole('tab', { name: 'Citations' }));
    expect(screen.getByRole('tab', { name: 'Answer' }).getAttribute('aria-selected')).toBe('true');
  });
});

describe('QA-CLASSIC-FU-1: the Recents fold', () => {
  it('is a button named Recents that says whether its list is open', () => {
    const onToggle = vi.fn();
    const { rerender } = render(h(Theme, null, h(RecentsToggle, { collapsed: true, onToggle, controls: 'recents-1' })));
    const fold = screen.getByRole('button', { name: 'Recents' });
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(fold.getAttribute('aria-controls')).toBe('recents-1');
    fireEvent.click(fold);
    expect(onToggle).toHaveBeenCalledTimes(1);
    rerender(h(Theme, null, h(RecentsToggle, { collapsed: false, onToggle, controls: 'recents-1' })));
    expect(screen.getByRole('button', { name: 'Recents' }).getAttribute('aria-expanded')).toBe('true');
  });
});
