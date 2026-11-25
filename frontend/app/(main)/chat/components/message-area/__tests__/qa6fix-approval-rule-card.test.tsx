/**
 * QA6-F6: a card raised by a layer's own approval rule (an org's "ask before
 * writes") says whose rule asks. With the run's context it also says what
 * would be sent and where, as the flow card does; without it, it shows the
 * call as the plain card does. A rule or a layer this page does not know
 * keeps today's card.
 */
import React from 'react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard, approvalWhyKey } from '../approval-card';
import { RULE_LAYERS } from '@/lib/contracts/harness/generated/core/enums';
import { toApprovalReason, toRuleLayer } from '../../../approval-preview';
import type { ApprovalReason, PendingApproval } from '../../../types';

const h = React.createElement;
const LOCALES = ['de-DE', 'en-IN', 'en-US', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
const LAYERS = RULE_LAYERS;
const RULES = ['writes', 'effect_class', 'tool'] as const;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const ORG_RULE: ApprovalReason = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false };
const ORG_SENTENCE = 'Your organization requires approval before an agent does this. Nothing happens until you decide.';

function approval(reason: ApprovalReason | undefined, status: PendingApproval['status'] = 'pending'): PendingApproval {
  return {
    interactionId: 'ix.approve.t1.0',
    toolCallId: 'call-1',
    toolName: 'jira__create_issue',
    args: JSON.stringify({ project: 'OPS', summary: 'Rotate the key', token: 'ghp_abcdefghijklmnopqrstuvwxyz0123456789' }),
    argsSummary: null,
    status,
    errorCode: null,
    ...(reason !== undefined ? { reason } : {}),
  };
}

function renderCard(reason: ApprovalReason | undefined, status: PendingApproval['status'] = 'pending') {
  const onDecide = vi.fn();
  render(h(Theme, null, h(ApprovalCard, { approval: approval(reason, status), onDecide, conversationChoice: true })));
  return onDecide;
}

const catalogue = (locale: string): Record<string, string> =>
  (JSON.parse(readFileSync(join(__dirname, '../../../../../../lib/i18n/locales', `${locale}.json`), 'utf8')) as {
    approvalCard: { policy: Record<string, string> };
  }).approvalCard.policy;

describe('ApprovalCard for a layer\'s own approval rule (QA6-F6)', () => {
  it('F6-9: says whose rule asks and shows the call as the plain card does', () => {
    renderCard(ORG_RULE);
    const card = screen.getByRole('region', { name: 'Create issue in Jira?' });
    expect(screen.getByTestId('approval-why').textContent).toBe(ORG_SENTENCE);
    expect(card.getAttribute('aria-describedby')).toBe(screen.getByTestId('approval-why').id);
    const args = screen.getByTestId('approval-preview').textContent ?? '';
    expect(args).toContain('Rotate the key');
    expect(args).not.toContain('ghp_abcdefghijklmnopqrstuvwxyz0123456789');
    expect(screen.queryByText('The agent wants to run this tool. Nothing happens until you decide.')).toBeNull();
    // UXINT-3: every rule shows what will be sent as labelled values; only a rule that read something lists sources.
    expect(screen.queryByTestId('approval-sources')).toBeNull();
  });

  it('F6-9: the rule is never allowed for the conversation, and the decision is the plain one', () => {
    const onDecide = renderCard({ ...ORG_RULE, conversationGrant: true });
    expect(screen.queryByRole('button', { name: /for this conversation: / })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Allow once: Create issue' }));
    expect(onDecide).toHaveBeenCalledWith('approved');
  });

  it('F6-9: with the run\'s context it says what would be sent and where, under the same sentence', () => {
    renderCard({ ...ORG_RULE, sensitivity: 'private', sources: [{ title: 'Q3 plan', recordId: 'rec-1' }], destination: 'OPS' });
    expect(screen.getByRole('region', { name: 'Create issue to OPS in Jira?' })).toBeTruthy();
    expect(screen.getByTestId('approval-why').textContent).toBe(ORG_SENTENCE);
    expect(screen.getByTestId('approval-preview').textContent).toContain('Rotate the key');
    expect(screen.getByRole('link', { name: 'Q3 plan' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /for this conversation: / })).toBeNull();
  });

  it('F6-9: the sentence stays once it is decided', () => {
    renderCard(ORG_RULE, 'approved');
    expect(screen.getByTestId('approval-why').textContent).toBe(ORG_SENTENCE);
    expect(screen.getByTestId('approval-card').getAttribute('data-status')).toBe('approved');
  });

  it.each(RULES)('F6-9: %s has one sentence per layer, and a general one for a layer this page does not know', (rule) => {
    const keys = LAYERS.map((layer) => approvalWhyKey({ ...ORG_RULE, rule, layer }));
    expect(keys).toEqual([
      'approvalCard.policy.whyDeployment',
      'approvalCard.policy.whyOrg',
      'approvalCard.policy.whyAgent',
      'approvalCard.policy.whyEngine',
      'approvalCard.policy.why',
    ]);
    expect(approvalWhyKey({ ...ORG_RULE, rule, layer: undefined })).toBe('approvalCard.policy.why');
  });

  it('F6-7: a rule this page does not know keeps today\'s card', () => {
    renderCard({ ...ORG_RULE, rule: 'quota' });
    expect(screen.queryByTestId('approval-why')).toBeNull();
    expect(screen.getByText('The agent wants to run this tool. Nothing happens until you decide.')).toBeTruthy();
    expect(approvalWhyKey({ ...ORG_RULE, rule: 'quota' })).toBeNull();
    expect(approvalWhyKey(undefined)).toBeNull();
  });

  it('F6-7: the flow and engine cards read as before, with or without a layer', () => {
    const flow: ApprovalReason = { rule: 'flow_external_write', sensitivity: 'private', sources: [], moreSources: 0, conversationGrant: true };
    expect(approvalWhyKey(flow)).toBe('approvalCard.flow.whyPrivate');
    expect(approvalWhyKey({ ...flow, layer: 'flow' })).toBe('approvalCard.flow.whyPrivate');
    expect(approvalWhyKey({ ...flow, rule: 'engine_writes', layer: 'engine', sensitivity: 'public' })).toBe('approvalCard.engine.why');
  });
});

describe('a reason from a response (QA6-F6)', () => {
  it('F6-7: a layer\'s rule needs no sensitivity; a layer or sensitivity nobody knows is not kept', () => {
    expect(toApprovalReason({ rule: 'writes', layer: 'org' })).toEqual(ORG_RULE);
    expect(toApprovalReason({ rule: 'writes', layer: 'galaxy' })).toEqual({ rule: 'writes', sources: [], moreSources: 0, conversationGrant: false });
    expect(toApprovalReason({ rule: 'writes', layer: 'org', sensitivity: 'secret' })).toBeNull();
    expect(toApprovalReason({ layer: 'org' })).toBeNull();
    expect(toApprovalReason({ rule: 'flow_external_write', sensitivity: 'unknown' })).toMatchObject({ rule: 'flow_external_write', sensitivity: 'unknown' });
  });

  it('F6-7: only the schema\'s layers are layers', () => {
    expect([...LAYERS]).toEqual(['deployment', 'org', 'agent', 'engine', 'flow']);
    expect(LAYERS.map(toRuleLayer)).toEqual([...LAYERS]);
    for (const not of ['', 'team', 'toString', 'constructor', 3, null, undefined]) expect(toRuleLayer(not)).toBeUndefined();
  });
});

describe('approvalCard.policy copy (QA6-F6)', () => {
  const english = catalogue('en-US');

  it('F6-10: every key the card uses is worded in all ten locales', () => {
    const used = new Set(
      RULES.flatMap((rule) => [...LAYERS, undefined].map((layer) => approvalWhyKey({ ...ORG_RULE, rule, layer }) as string)),
    );
    for (const key of used) {
      const leaf = key.replace('approvalCard.policy.', '');
      for (const locale of LOCALES) {
        expect(catalogue(locale)[leaf], `${locale} ${key}`).toMatch(/\S/);
      }
    }
  });

  it('F6-10: no locale has a key another lacks, and only the two English ones share their wording', () => {
    for (const locale of LOCALES) {
      const copy = catalogue(locale);
      expect(Object.keys(copy).sort(), locale).toEqual(Object.keys(english).sort());
      if (!locale.startsWith('en-')) {
        for (const key of Object.keys(english)) expect(copy[key], `${locale} ${key} is translated`).not.toBe(english[key]);
      }
    }
  });
});
