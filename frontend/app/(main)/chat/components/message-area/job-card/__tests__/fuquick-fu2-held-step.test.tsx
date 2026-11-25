/**
 * FUQUICK-FU-2 (TOOLWORDS-FU-2's second half): the classic job card's inline steps drew an engine's gated call as a
 * running row under its ask. On the lane's frames (FIX-TOOLWORDS `fixed`), the call that waits on its ask reads as
 * waiting for approval, refused as blocked, and once answered as the engine tells it; the ask's card is unchanged.
 */
import React from 'react';
import { beforeAll, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { MessagePart, SSEEngineProgressEvent } from '../../../../types';
import { stepsOf, withHeldAsks } from '../../child-run-panel';
import { AgentActivityTimeline } from '../../agent-activity';
import fixture from '../../../activity-panel/__tests__/data/fu5-opencode-ticket-frames.json';

const fixed = fixture.fixed as SSEEngineProgressEvent[];
const ASK = 'ix.approve.kaaa8862305c13d14f9b79ade140095fd8bc8587f';
const steps = (frames = fixed): MessagePart[] => stepsOf([{ type: 'engine_task', runId: 'job', frames }], false).steps;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('FUQUICK-FU-2: a gated call in the job card reads as waiting on its ask', () => {
  it('FQ2-1: the call its ask holds waits for approval; refused it is blocked; answered the engine says how it went', () => {
    expect(steps()).toEqual([expect.objectContaining({ toolCallId: 'call_e2e_33', interactionId: ASK, status: 'running' })]);
    expect(withHeldAsks(steps(), [{ interactionId: ASK, status: 'pending' }])[0]).toMatchObject({ status: 'awaiting_approval' });
    expect(withHeldAsks(steps(), [{ interactionId: ASK, status: 'sending' }])[0]).toMatchObject({ status: 'awaiting_approval' });
    expect(withHeldAsks(steps(), [{ interactionId: ASK, status: 'denied' }])[0]).toMatchObject({ status: 'blocked' });
    expect(withHeldAsks(steps(), [{ interactionId: ASK, status: 'approved' }])[0]).toMatchObject({ status: 'running' });
    const done: SSEEngineProgressEvent = { ...fixed[1]!, kind: 'tool_result', data: { callId: 'call_e2e_33', status: 'completed', result: 'filed' } };
    expect(withHeldAsks(steps([...fixed, done]), [{ interactionId: ASK, status: 'pending' }])[0], 'a finished call keeps its result').toMatchObject({
      status: 'completed',
    });
  });

  it('FQ2-2: the later telling wins, and an ask that names no step changes nothing', () => {
    const told = [
      { interactionId: ASK, status: 'pending' },
      { interactionId: ASK, status: 'denied' },
    ];
    expect(withHeldAsks(steps(), told)[0]).toMatchObject({ status: 'blocked' });
    const other = steps();
    expect(withHeldAsks(other, [{ interactionId: 'ix.other', status: 'pending' }])).toEqual(other);
    expect(withHeldAsks(other, [])).toBe(other);
  });

  it('FQ2-3: drawn, the held step shows the approval chip in plain words and no spinning row', () => {
    const spinning = () => document.querySelectorAll('[style*="spin"]').length;
    const { unmount } = render(
      <Theme>
        <AgentActivityTimeline parts={steps()} isNested />
      </Theme>,
    );
    expect(spinning(), 'unfolded, the call spins as if it ran').toBeGreaterThan(0);
    unmount();
    render(
      <Theme>
        <AgentActivityTimeline parts={withHeldAsks(steps(), [{ interactionId: ASK, status: 'pending' }])} isNested />
      </Theme>,
    );
    expect(screen.getAllByText(i18n.t('chat.toolCard.needsApproval')).length).toBeGreaterThan(0);
    expect(spinning()).toBe(0);
  });
});
