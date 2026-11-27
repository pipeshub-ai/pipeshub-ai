/**
 * ADR-SCH-03 D3/D4 on the Schedules page: a schedule changed from a chat links to that chat, and one whose next run
 * was skipped says so.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { toSchedule } from '../api';
import { ScheduleRow } from '../components/schedule-row';
import type { Schedule } from '../types';
import { scheduleView } from './fixtures';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const listed = (over: Record<string, unknown> = {}): Schedule => {
  const schedule = toSchedule(scheduleView(over));
  if (schedule === null) throw new Error('not listed');
  return schedule;
};
const renderRow = (schedule: Schedule, orgView = false) =>
  render(
    <Theme>
      <ScheduleRow schedule={schedule} agentName="Support agent" orgView={orgView} onAction={async () => {}} />
    </Theme>,
  );

const CHANGED = { conversationId: 'conv-7', agentKey: 'agent-a', at: '2026-11-27T10:00:00.000Z' };

describe('reading the new fields', () => {
  it('keeps where it was last changed from and the skipped slot', () => {
    const schedule = listed({ lastChangedFrom: CHANGED, skipNextAt: '2026-11-30T17:00:00.000Z' });
    expect(schedule.lastChangedFrom).toEqual(CHANGED);
    expect(schedule.skipNextAt).toBe('2026-11-30T17:00:00.000Z');
  });

  it('reads anything else, or an older server, as none', () => {
    expect(listed().lastChangedFrom).toBeNull();
    expect(listed().skipNextAt).toBeNull();
    expect(listed({ lastChangedFrom: { agentKey: 'a' }, skipNextAt: 'soon' })).toMatchObject({ lastChangedFrom: null, skipNextAt: null });
    expect(listed({ lastChangedFrom: { conversationId: 'c', at: 'never' } }).lastChangedFrom).toEqual({
      conversationId: 'c',
      agentKey: null,
      at: null,
    });
  });
});

describe('the row', () => {
  it('links to the chat it was last changed from', () => {
    renderRow(listed({ lastChangedFrom: CHANGED }));
    const link = screen.getByTestId('schedule-last-changed-from');
    expect(link.textContent).toBe('Last changed from a chat');
    expect(link.getAttribute('href')).toContain('conversationId=conv-7');
    expect(link.getAttribute('href')).toContain('agentId=agent-a');
  });

  it('an admin’s view of someone else’s schedule never links into their chat', () => {
    renderRow(listed({ lastChangedFrom: CHANGED }), true);
    expect(screen.queryByTestId('schedule-last-changed-from')).toBeNull();
  });

  it('says its next run is skipped', () => {
    renderRow(listed({ skipNextAt: '2026-11-30T17:00:00.000Z' }));
    expect(screen.getByTestId('schedule-skip-next').textContent).toMatch(/^The run at .+ will be skipped$/);
  });

  it('says nothing of either where there is none', () => {
    renderRow(listed());
    expect(screen.queryByTestId('schedule-skip-next')).toBeNull();
    expect(screen.queryByTestId('schedule-last-changed-from')).toBeNull();
  });
});
