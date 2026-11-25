/**
 * DEEPROUTE: a run that hands its work to the coding engine on its own names it and says why, "Handed to Claude
 * Code: this needs research across many sources", with the goal it gave under it; the engine's steps open under it
 * as any code task's do. A code task the model chose keeps "Code task: <goal>".
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { SubAgentGroup } from '../agent-activity';
import { createAGUIEventHandler } from '../../../agui-event-handler';
import { handoffLabel, HANDOFF_REASONS, toCodeTaskHandoff } from '../../../handoff';
import type { MessagePart } from '../../../types';
import type { StreamMessageCallbacks } from '../../../api';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const row = (part: MessagePart) => render(<Theme><SubAgentGroup part={part} /></Theme>);
const GOAL = 'Triage the open pull requests of pipeshub-ai';

describe('DEEPROUTE: the hand-off row', () => {
  it('HR-1: names the engine and the reason, with the goal under it', () => {
    row({
      type: 'sub_agent', runId: 'c1', roleName: 'pipeshub-subagent', codeTaskGoal: GOAL, status: 'running', parts: [],
      codeTaskHandoff: { trigger: 'upfront', reason: 'research', engine: 'claude_code' },
    });
    expect(screen.getByText('Handed to Claude Code: this needs research across many sources')).toBeTruthy();
    expect(screen.getByTestId('sub-agent-handoff-goal').textContent).toBe(GOAL);
    expect(screen.queryByText(`Code task: ${GOAL}`)).toBeNull();
  });

  it('HR-2: without an engine name it says "the coding engine"; an unknown reason reads as many steps', () => {
    const t = i18n.t.bind(i18n);
    expect(handoffLabel(t, { trigger: 'escalation', reason: 'many_steps' })).toBe(
      'Handed to the coding engine: this needs many steps',
    );
    expect(handoffLabel(t, { trigger: 'escalation', reason: 'vibes', engine: 'codex' })).toBe(
      'Handed to Codex: this needs many steps',
    );
  });

  it('HR-3: every reason the worker journals has its words, in every shipped locale', async () => {
    for (const lang of ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-TW', 'zh-SG', 'cs-CZ']) {
      await i18n.changeLanguage(lang);
      for (const reason of HANDOFF_REASONS) {
        const key = `agentActivity.handoff.reason.${reason}`;
        expect(i18n.exists(key), `${lang} ${key}`).toBe(true);
      }
    }
    await i18n.changeLanguage('en-US');
  });

  it('HR-4: a code task the model chose keeps "Code task: <goal>"', () => {
    row({ type: 'sub_agent', runId: 'c2', roleName: 'pipeshub-subagent', codeTaskGoal: GOAL, status: 'completed', parts: [] });
    expect(screen.getByText(`Code task: ${GOAL}`)).toBeTruthy();
  });

  it('HR-5: the live stream carries it onto the nested group', () => {
    expect(toCodeTaskHandoff({ trigger: 'upfront' })).toBeNull();
    const onParts = vi.fn();
    const handle = createAGUIEventHandler({ onParts } as unknown as StreamMessageCallbacks);
    const frame = (type: string, fields: Record<string, unknown>) => ({ event: type, data: { type, ...fields } });
    handle(frame('RUN_STARTED', { runId: 'root' }));
    handle(frame('RUN_STARTED', {
      runId: 'child', parentRunId: 'root', codeTaskGoal: GOAL,
      codeTaskHandoff: { trigger: 'escalation', reason: 'context', engine: 'opencode', extra: 'dropped' },
    }));
    handle(frame('TEXT_MESSAGE_START', { runId: 'child' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'child', delta: 'Reading the PRs' }));
    const parts = onParts.mock.calls.at(-1)?.[0] as MessagePart[];
    const group = parts.find((p) => p.type === 'sub_agent');
    expect(group?.codeTaskHandoff).toEqual({ trigger: 'escalation', reason: 'context', engine: 'opencode' });
    expect(group?.codeTaskGoal).toBe(GOAL);
  });
});
