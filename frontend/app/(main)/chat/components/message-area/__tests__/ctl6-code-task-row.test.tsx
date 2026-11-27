/**
 * CTL-6: a coder agent's handoff to the engine is a sub-agent row named by its task, "Code task: <goal>"; its own
 * steps and files open under it (CTL-4). Other sub-agents keep their role name.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { RunNotices, SubAgentGroup } from '../agent-activity';
import type { MessagePart } from '../../../types';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const row = (part: MessagePart) => render(<Theme><SubAgentGroup part={part} /></Theme>);

describe('CTL-6: the code_task handoff row', () => {
  it('reads "Code task: <goal>"', () => {
    row({ type: 'sub_agent', runId: 'c1', roleName: 'pipeshub-subagent-policy', codeTaskGoal: 'Write travel_policy.md', status: 'completed', parts: [] });
    expect(screen.getByText('Code task: Write travel_policy.md')).toBeTruthy();
    expect(screen.queryByText('pipeshub-subagent-policy')).toBeNull();
  });

  it('another sub-agent keeps its role', () => {
    row({ type: 'sub_agent', runId: 'c2', roleName: 'pipeshub-subagent-analysis', status: 'completed', parts: [] });
    expect(screen.getByText('pipeshub-subagent-analysis')).toBeTruthy();
  });
});

describe('CTL-6: a refused hand-off shows the person what to do (the CTL-5b notice)', () => {
  it.each([
    ['account_missing', 'chatStream.admission.accountMissing'],
    ['model_access', 'chatStream.admission.modelAccess'],
    ['engine_profile', 'chatStream.admission.engineProfile'],
    ['engine_unavailable', 'chatStream.admission.engineUnavailable'],
  ])('%s reads as the admission refusal does', (code, key) => {
    render(<Theme><RunNotices parts={[{ type: 'notice', code, runId: 'r' }]} /></Theme>);
    expect(screen.getByText(i18n.t(key))).toBeTruthy();
  });
});
