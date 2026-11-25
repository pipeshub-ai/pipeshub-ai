import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { AgentActivityTimeline } from '../agent-activity';
import { VIRTUALIZE_AFTER } from '../../virtual-rows';
import type { MessagePart } from '../../../types';

afterEach(() => cleanup());

const h = React.createElement;

/** C5.01 (event history, EV-15): a run's own steps, each a row of its own (narration never groups). */
function steps(count: number): MessagePart[] {
  return Array.from({ length: count }, (_, i) => ({ type: 'text', content: `narrated step ${i}.`, settled: true }) as MessagePart);
}

/** The timeline's own rows (a step's body may hold rows of its own). */
function rows(container: HTMLElement): number {
  return [...container.querySelectorAll('.agent-activity-enter')].filter(
    (row) => !row.parentElement?.closest('.agent-activity-enter'),
  ).length;
}

// The timeline is virtualized (EV-15), not paged: every step is reached by scrolling, with no extra click, and the
// page stays bounded however long the run.
describe('C5.01 TL-1: a long run renders a bounded window and needs no paging control', () => {
  it('a 10,000-step run is virtualized: far fewer rows than steps, and no "earlier steps" button', () => {
    const { container } = render(h(Theme, null, h(AgentActivityTimeline, { parts: steps(10_000) })));
    expect(screen.getByTestId('agent-activity-rows').dataset.virtualized).toBe('true');
    expect(rows(container)).toBeLessThan(VIRTUALIZE_AFTER);
    expect(screen.queryByRole('button', { name: /earlier step/ })).toBeNull();
  });

  it('shows a short run whole, with no paging control', () => {
    const { container } = render(h(Theme, null, h(AgentActivityTimeline, { parts: steps(12) })));
    expect(rows(container)).toBe(12);
    expect(screen.queryByRole('button', { name: /earlier step/ })).toBeNull();
  });

  it('a run at the threshold renders every step, the latest included', () => {
    const { container } = render(h(Theme, null, h(AgentActivityTimeline, { parts: steps(VIRTUALIZE_AFTER) })));
    expect(rows(container)).toBe(VIRTUALIZE_AFTER);
    expect(screen.getByText(`narrated step ${VIRTUALIZE_AFTER - 1}.`)).toBeTruthy();
  });
});
