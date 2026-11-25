/**
 * R40-TEXT SEC-B-04 on screen (ADR-TEXT-01): what an injected call would make the approval card say, and what the
 * card says instead. A hidden character is a set-apart marker with a note in words; a lookalike host is shown in its
 * real form with "looks like" beside it; a recipient is never cut behind "Show all"; an MCP server named like one of
 * PipesHub's own apps is marked as a server; plain text reads as it always did.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import { ApprovalInteraction } from '../interaction-card';
import { PREVIEW_CHARS } from '../../../approval-preview';
import { appOf } from '../../../interaction-wording';
import type { ApprovalReason, PendingApproval } from '../../../types';

const h = React.createElement;
const RLO = '\u202e';
const PDF = '\u202c';
const ZWSP = '\u200b';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

function flowReason(destination: string): ApprovalReason {
  return { rule: 'flow_external_write', sensitivity: 'private', sources: [], moreSources: 0, destination, conversationGrant: true };
}

function call(args: Record<string, unknown>, extra: Partial<PendingApproval> = {}): PendingApproval {
  return {
    interactionId: 'ix.approve.t1.0',
    toolCallId: 'call-1',
    toolName: 'gmail__send_email',
    args: JSON.stringify(args),
    status: 'pending',
    errorCode: null,
    ...extra,
  };
}

const show = (approval: PendingApproval) =>
  render(h(Theme, null, h(ApprovalCard, { approval, onDecide: () => undefined, conversationChoice: true })));

describe('SEC-B-04: the approval card shows exactly what will happen', () => {
  it('a bidi override in a recipient is a marker, set apart, with a note in words', () => {
    const { container } = show(call({ to: [`${RLO}moc.live@bob${PDF}`], text: 'hi' }, { reason: flowReason(`${RLO}moc.live@bob${PDF}`) }));
    expect(container.textContent).not.toMatch(/[\u202a-\u202e\u2066-\u2069]/);
    expect(screen.getAllByTestId('display-marker').length).toBeGreaterThan(0);
    expect(screen.getByTestId('approval-text-check-hidden').textContent).toMatch(/shown as codes/);
  });

  it('a zero-width character splitting a domain is a marker', () => {
    const { container } = show(call({ to: `ceo@internal${ZWSP}.example`, text: 'hi' }));
    expect(container.textContent).not.toContain(ZWSP);
    expect(container.textContent).toContain('ceo@internal⟨U+200B⟩.example');
  });

  it('a lookalike host is shown in punycode with what it looks like beside it', () => {
    const { container } = show(call({ to: 'ceo@pіpeshub.com', text: 'hi' }, { reason: flowReason('ceo@xn--ppeshub-rog.com') }));
    expect(screen.getByTestId('interaction-heading').textContent).toContain('xn--ppeshub-rog.com');
    expect(container.textContent).not.toContain('pіpeshub.com?');
    expect(screen.getByTestId('approval-text-check-host').textContent).toBe(
      'xn--ppeshub-rog.com mixes alphabets and looks like pіpeshub.com. It is shown in its real form because it may not be the site it seems to be.',
    );
  });

  it('a lookalike host inside a message body is shown in its real form too', () => {
    show(call({ to: 'ana@corp.example', text: 'Sign in at https://pіpeshub.com/login' }));
    expect(screen.getAllByTestId('approval-preview-value').map((v) => v.textContent).join('\n')).toContain('https://xn--ppeshub-rog.com/login');
  });

  it('a long recipient list is never cut behind "Show all"', () => {
    const to = Array.from({ length: 30 }, (_, i) => `colleague${i}@corp.example`).concat('drop@attacker.example');
    show(call({ to, text: 'hi' }));
    const values = screen.getAllByTestId('approval-preview-value').map((v) => v.textContent ?? '');
    expect(values.some((v) => v.includes('drop@attacker.example'))).toBe(true);
    expect(values.join('').length).toBeGreaterThan(PREVIEW_CHARS);
  });

  it('an MCP server named like a PipesHub app is marked as a server', () => {
    expect(appOf({ toolName: 'mcp_slack_send_message', displayName: 'Slack · Send message' })).toBe('Slack (MCP)');
    expect(appOf({ toolName: 'api_slack_post', displayName: 'slack · Post' })).toBe('slack (API)');
    expect(appOf({ toolName: 'slack__send_message' })).toBe('Slack');
    expect(appOf({ toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket' })).toBe('E2Esaas');
    show(call({ text: 'hi' }, { toolName: 'mcp_slack_send_message', displayName: 'Slack · Send message' }));
    expect(screen.getByTestId('interaction-heading').textContent).toBe('Send message in Slack (MCP)?');
  });

  it('plain text reads as it always did, with no note', () => {
    show(call({ to: ['Renée Müller <renee@münchen.de>'], text: 'Hallo, danke!' }, { reason: flowReason('renee@münchen.de') }));
    expect(screen.queryByTestId('approval-text-check')).toBeNull();
    expect(screen.queryAllByTestId('display-marker')).toHaveLength(0);
    expect(screen.getByTestId('interaction-heading').textContent).toContain('renee@münchen.de');
  });

  it('"Allow all" reads every call, so one hidden recipient among many still raises the note', () => {
    const items = Array.from({ length: 4 }, (_, i) =>
      call({ to: i === 3 ? `x${RLO}@corp.example` : `p${i}@corp.example`, text: 'hi' }, { interactionId: `ix.approve.t1.${i}`, toolCallId: `c${i}` }),
    );
    render(
      h(Theme, null,
        h(ApprovalInteraction, {
          group: { key: 'g', items },
          state: 'active',
          onDecide: () => undefined,
          onDecideAll: () => undefined,
          conversationChoice: false,
          paused: false,
        })),
    );
    expect(screen.getByTestId('approval-text-check-hidden')).toBeTruthy();
  });
});
