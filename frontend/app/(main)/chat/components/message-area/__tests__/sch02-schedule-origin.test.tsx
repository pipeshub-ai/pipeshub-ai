/**
 * ADR-SCH-02 D3: a conversation a schedule's firing started says so at its top, with the schedule's name linking to
 * Workspace > Schedules. Read from GET conversation detail `origin` behind a guard: anything else is no origin.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { scheduleOriginOf } from '../../../schedule-origin';
import { ScheduleOriginHeader, SCHEDULES_HREF } from '../schedule-origin-header';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const ORIGIN = { kind: 'schedule', scheduleId: 'sch-1', firingId: 'f-1', scheduleName: 'Monday digest' };

describe('the origin the conversation detail carries', () => {
  it('a schedule’s firing', () => {
    expect(scheduleOriginOf({ id: 'c1', origin: ORIGIN })).toEqual(ORIGIN);
    expect(scheduleOriginOf({ origin: { ...ORIGIN, scheduleName: undefined } })).toEqual({ ...ORIGIN, scheduleName: null });
    expect(scheduleOriginOf({ origin: { ...ORIGIN, scheduleName: '  ' } })?.scheduleName).toBeNull();
  });

  it.each([
    ['no origin (an older server, a person’s chat)', { id: 'c1' }],
    ['another kind', { origin: { ...ORIGIN, kind: 'chat' } }],
    ['no schedule id', { origin: { ...ORIGIN, scheduleId: '' } }],
    ['no firing id', { origin: { ...ORIGIN, firingId: 7 } }],
    ['not an object', { origin: 'schedule' }],
    ['no conversation', null],
  ])('%s is none', (_name, conversation) => {
    expect(scheduleOriginOf(conversation)).toBeNull();
  });

  it('a name with invisible characters is shown marked, never as written', () => {
    const origin = scheduleOriginOf({ origin: { ...ORIGIN, scheduleName: 'Digest‮gnp.exe' } });
    expect(origin?.scheduleName).not.toContain('‮');
  });
});

describe('the header', () => {
  it('names the schedule and links to Workspace > Schedules', () => {
    render(<Theme><ScheduleOriginHeader origin={{ ...ORIGIN, kind: 'schedule' }} /></Theme>);
    const header = screen.getByTestId('schedule-origin-header');
    expect(header.textContent).toBe('Started by the scheduleMonday digest');
    expect(screen.getByRole('link', { name: 'Monday digest' }).getAttribute('href')).toBe(SCHEDULES_HREF);
    expect(SCHEDULES_HREF).toBe('/workspace/schedules');
  });

  it('without a name it still says a schedule started it, and links to the schedules', () => {
    render(<Theme><ScheduleOriginHeader origin={{ ...ORIGIN, kind: 'schedule', scheduleName: null }} /></Theme>);
    expect(screen.getByTestId('schedule-origin-header').textContent).toBe('Started by a schedule ·Manage schedules');
    expect(screen.getByRole('link', { name: 'Manage schedules' }).getAttribute('href')).toBe('/workspace/schedules');
  });
});
