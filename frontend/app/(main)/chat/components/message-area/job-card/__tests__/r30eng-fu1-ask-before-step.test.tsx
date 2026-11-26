/**
 * R30ENG-FU-1: on the durable lane the job's ask reached the journal before the engine's line for the call it holds,
 * naming no call id, so the card's steps drew the held call as running (FIX-TOOLWORDS waited for "Needs approval" in
 * vain). The worker now names the ask on the step's line when the ask came first; the step then reads as waiting on
 * it, in the card's steps and in the Activity panel, as when the line comes first.
 */
import React from 'react';
import { beforeAll, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { MessagePart, SSEEngineProgressEvent } from '../../../../types';
import { stepsOf, withHeldAsks } from '../../child-run-panel';
import { AgentActivityTimeline } from '../../agent-activity';
import fixture from '../../../activity-panel/__tests__/data/r30eng-fu1-ask-before-step-frames.json';

const lane = fixture.lane as SSEEngineProgressEvent[];
const linked = fixture.linked as SSEEngineProgressEvent[];
const ASK = 'ix.approve.kd19a17212e9a0769cea4ac797595643c5136b330';
const steps = (frames: SSEEngineProgressEvent[]): MessagePart[] => stepsOf([{ type: 'engine_task', runId: 'job', frames }], false).steps;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('R30ENG-FU-1: an ask journaled before its step', () => {
  it('FU1-F1: control, the lane as it was: the step names no ask and keeps running under it', () => {
    const held = withHeldAsks(steps(lane), [{ interactionId: ASK, status: 'pending' }]);
    expect(held).toEqual([expect.objectContaining({ toolCallId: 'call_e2e_5', status: 'running' })]);
    expect(held[0]).not.toHaveProperty('interactionId');
  });

  it('FU1-F2: the line that names its ask is one step, waiting on it, in plain words', () => {
    const held = withHeldAsks(steps(linked), [{ interactionId: ASK, status: 'pending' }]);
    expect(held).toEqual([expect.objectContaining({ toolCallId: 'call_e2e_5', interactionId: ASK, status: 'awaiting_approval' })]);
    render(
      <Theme>
        <AgentActivityTimeline parts={held} isNested />
      </Theme>,
    );
    expect(screen.getAllByText(i18n.t('chat.toolCard.needsApproval')).length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[style*="spin"]').length).toBe(0);
  });
});
