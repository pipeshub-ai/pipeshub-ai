import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { ResponseTabs } from '../response-tabs';

afterEach(() => cleanup());

const strip = () =>
  render(
    <Theme>
      <ResponseTabs activeTab="answer" onTabChange={() => {}} sourcesCount={2} citationCount={3} spread />
    </Theme>,
  );

describe("ResponseTabs: an answer's own tabs below 1024px", () => {
  it('names no panel that is not on the page', () => {
    strip();
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(3);
    for (const tab of tabs) {
      const controls = tab.getAttribute('aria-controls');
      if (controls !== null) expect(document.getElementById(controls), `${tab.textContent} controls #${controls}`).not.toBeNull();
    }
  });

  it('the arrow keys move between the tabs, which keep one tab stop between them', async () => {
    strip();
    const tabs = screen.getAllByRole('tab');
    const [answer, sources] = tabs;
    expect(answer.getAttribute('aria-selected')).toBe('true');
    answer.focus();
    fireEvent.keyDown(answer, { key: 'ArrowRight' });
    // Radix moves the focus on the next tick.
    await waitFor(() => expect(document.activeElement).toBe(sources));
    expect(tabs.filter((tab) => tab.tabIndex === 0)).toEqual([sources]);
  });
});
