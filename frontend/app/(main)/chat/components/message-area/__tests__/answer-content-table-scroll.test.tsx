import React from 'react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, fireEvent, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';

afterEach(() => cleanup());

// No JSX here — see the same note in agent-activity.test.tsx.
const h = React.createElement;

const WIDE_CELL =
  'Temporary workaround was to export per project; the permanent fix queued large exports in the background with a download link';

const GFM_TABLE = [
  'The customers who reported export timeouts were:',
  '',
  '| Customer | What happened | Fix / workaround |',
  '| --- | --- | --- |',
  `| Northwind Traders | Full-workspace export timed out after ~30 seconds | ${WIDE_CELL} |`,
  `| Contoso | Repeated export timeouts on a ~61k-record workspace | ${WIDE_CELL} |`,
].join('\n');

const CSV_TABLE = [
  '```csv',
  'Customer,What happened,Fix',
  `Northwind Traders,Export timed out,"${WIDE_CELL}"`,
  '```',
].join('\n');

function renderAnswer(content: string) {
  return render(h(Theme, null, h(AnswerContent, { content, isStreaming: false })));
}

describe('AnswerContent — wide tables scroll inside the message', () => {
  it.each([
    ['a markdown table', GFM_TABLE],
    ['a csv block', CSV_TABLE],
  ])('puts %s in the visible-scrollbar scroll area', (_name, content) => {
    const { container } = renderAnswer(content);
    const table = container.querySelector('table');
    expect(table).not.toBeNull();
    const area = table!.parentElement as HTMLElement;
    expect(area.hasAttribute('data-table-scroll-area')).toBe(true);
    expect(area.style.overflowX).toBe('auto');
  });

  it('puts the fullscreen view\'s scroll box in the scroll area too', async () => {
    const { container } = renderAnswer(GFM_TABLE);
    fireEvent.click(container.querySelector('button[title]')!);
    const dialog = await screen.findByRole('dialog');
    // In fullscreen the outer box owns the overflow; the table's own box grows to fit.
    const scroller = Array.from(dialog.querySelectorAll<HTMLElement>('div')).find(
      (el) => el.style.overflow === 'auto',
    );
    expect(scroller).toBeDefined();
    expect(scroller!.hasAttribute('data-table-scroll-area')).toBe(true);
  });

  it('keeps the scrollbar styled visible for that area', () => {
    // jsdom doesn't load globals.css, so check the rule the attribute relies on exists.
    const css = readFileSync(path.resolve(__dirname, '../../../../../globals.css'), 'utf8');
    expect(css).toMatch(/\[data-table-scroll-area\]::-webkit-scrollbar\s*\{[^}]*height:\s*8px/);
    // An inherited scrollbar-color would make Chrome drop the webkit rules for overlay bars.
    expect(css).toMatch(/^\[data-table-scroll-area\]\s*\{\s*scrollbar-color:\s*auto;\s*\}/m);
    expect(css).not.toMatch(/^\[data-table-scroll-area\]\s*\{[^}]*scrollbar-width/m);
    expect(css).toMatch(
      /@supports not selector\(::-webkit-scrollbar-thumb\)\s*\{\s*\[data-table-scroll-area\]\s*\{[^}]*scrollbar-width:\s*thin/,
    );
  });
});
