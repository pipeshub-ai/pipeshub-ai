/**
 * GA-02, GA-03, GA-04: what the run gates journal reads in the chat, in plain words. A dry run says nothing was done;
 * a verified or unverified run says so beside its answer (with the first reason); a plan waiting for approval says
 * why on the approval card.
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import type { MessagePart } from '../../../types';
import { RunNotices } from '../agent-activity';
import { approvalWhyKey } from '../approval-card';

afterEach(() => cleanup());

const notes = async (parts: MessagePart[]): Promise<string[]> => {
  await i18n.changeLanguage('en-US');
  render(<Theme><RunNotices parts={parts} /></Theme>);
  return screen.getAllByRole('note').map((note) => note.textContent ?? '');
};

describe('run gates in the chat', () => {
  it('GA-02: a dry run says once that nothing was actually done', async () => {
    const dry: MessagePart = { type: 'notice', code: 'dry_run', runId: 'run-1' };
    expect(await notes([dry, { ...dry }])).toEqual([
      'This was a dry run: nothing in this answer was actually changed, sent or deleted.',
    ]);
  });

  it('GA-04: an unverified run names the first check that failed', async () => {
    const part: MessagePart = {
      type: 'notice', code: 'unverified', item: 'The command pytest exited with code 1.', runId: 'run-1',
    };
    expect(await notes([part])).toEqual(['Not verified: The command pytest exited with code 1.']);
  });

  it('GA-04: a verified run says its checks passed', async () => {
    expect(await notes([{ type: 'notice', code: 'verified', runId: 'run-1' }])).toEqual([
      'Checked: this answer passed its checks.',
    ]);
  });

  it('GA-03: a plan waiting for approval says why', () => {
    expect(approvalWhyKey({ rule: 'plan_approval', toolId: 'harness@gate/submit_plan', conversationGrant: false } as never)).toBe(
      'approvalCard.plan.why',
    );
  });
});
