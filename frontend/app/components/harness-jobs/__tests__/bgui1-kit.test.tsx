/**
 * BGUI-1: the background-agent kit drawn in classic's look. The agent tile wears its maker's mark (or the initial, or
 * the robot), the corner badge has four looks for the six status words, the glyph spins for every live state and
 * spends a colour only on a failure, and the group label carries its count. All of it is decorative except the label.
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { JOB_STATUSES } from '@/lib/harness-jobs/job-status';
import { AgentTile, GroupLabel, StatusGlyph, badgeOf } from '..';

afterEach(() => cleanup());

const view = (node: React.ReactNode) => render(<Theme>{node}</Theme>);

describe('BGUI-1: the agent tile', () => {
  it('wears the maker mark, the initial for an engine with none, and the robot with no engine', () => {
    const { container } = view(
      <>
        <AgentTile agent="Claude Code" />
        <AgentTile agent="Codex" size={28} />
        <AgentTile agent="Gemini" />
        <AgentTile agent="Background agent" />
      </>,
    );
    const tiles = screen.getAllByTestId('agent-tile');
    expect(tiles.map((t) => t.getAttribute('data-agent'))).toEqual(['Claude Code', 'Codex', 'Gemini', 'Background agent']);
    expect(tiles[0].querySelector('[data-agent-mark="claude"]')).toBeTruthy();
    expect(tiles[1].querySelector('[data-agent-mark="openai"]')).toBeTruthy();
    expect(tiles[1].style.width).toBe('28px');
    expect(tiles[2].textContent).toBe('G');
    expect(tiles[3].textContent).toBe('smart_toy');
    // Decorative: the words beside it name the agent.
    for (const tile of tiles) expect(tile.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelectorAll('[data-testid="job-tile-badge"]')).toHaveLength(0);
  });

  it('puts the status on its corner: a tick, a !, a stop, or a spinner while it goes', () => {
    view(
      <>
        {JOB_STATUSES.map((s) => (
          <AgentTile key={s} agent="Claude Code" status={s} />
        ))}
      </>,
    );
    expect(screen.getAllByTestId('job-tile-badge').map((b) => b.getAttribute('data-badge'))).toEqual([
      'live',
      'live',
      'live',
      'done',
      'failed',
      'stopped',
    ]);
    expect(JOB_STATUSES.map(badgeOf)).toEqual(['live', 'live', 'live', 'done', 'failed', 'stopped']);
  });
});

describe('BGUI-1: the status glyph', () => {
  it('spins for every live state but Starting, and only a failure is red', () => {
    const { container } = view(
      <>
        {JOB_STATUSES.map((s) => (
          <span key={s} data-s={s}>
            <StatusGlyph status={s} />
          </span>
        ))}
      </>,
    );
    const glyph = (s: string) => container.querySelector(`[data-s="${s}"]`) as HTMLElement;
    expect(glyph('working').querySelector('[data-glyph="spinner"]')).toBeTruthy();
    expect(glyph('needsYou').querySelector('[data-glyph="spinner"]')).toBeTruthy();
    expect(glyph('upNext').textContent).toBe('schedule');
    expect(glyph('done').textContent).toBe('check');
    expect(glyph('stopped').textContent).toBe('stop_circle');
    const failed = glyph('couldntFinish').firstElementChild as HTMLElement;
    expect(failed.style.color).toBe('var(--red-11)');
    for (const s of ['upNext', 'done', 'stopped']) {
      expect((glyph(s).firstElementChild as HTMLElement).style.color).toBe('var(--gray-11)');
    }
    expect(container.textContent).not.toMatch(/progress_activity/);
  });
});

describe('BGUI-1: the group label', () => {
  it('names the group with its count, and heads a region when asked to', () => {
    view(
      <>
        <GroupLabel count={1}>Needs you</GroupLabel>
        <GroupLabel as="h3" id="running">
          Running
        </GroupLabel>
      </>,
    );
    const [first, second] = screen.getAllByTestId('group-label');
    expect(first.textContent).toBe('Needs you 1');
    expect(first.style.textTransform).toBe('uppercase');
    expect(second.tagName).toBe('H3');
    expect(screen.getByRole('heading', { name: 'Running' }).id).toBe('running');
  });
});
