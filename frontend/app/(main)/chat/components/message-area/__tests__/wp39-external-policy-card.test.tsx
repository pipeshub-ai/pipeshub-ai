/**
 * WP-39 (G-40, SEC-CR-07 x AP-06).
 *
 * WP39-UI-1 a card the org's policy webhook raised (rule `external_policy`) says so in plain words, in every
 *           locale, and offers Allow once and Deny only, never "Allow for this conversation".
 * WP39-UI-2 webhook asks of one batch are never grouped under "Allow all": each is decided on its own card.
 * WP39-UI-3 an edit refused for a pasted secret (EDIT_HAS_SECRET) tells the person to save it as a credential, in
 *           the composer dialog's words, in every locale.
 */
import React from 'react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard, approvalWhyKey, offersConversation, REFUSAL_KEYS } from '../approval-card';
import { HOLD_RULES } from '@/lib/contracts/harness/generated/core/enums';
import { harnessHttpCode } from '@/lib/contracts/harness/codes';
import { groupApprovals } from '../../../interactions';
import type { ApprovalReason, PendingApproval } from '../../../types';

const h = React.createElement;
const LOCALE_DIR = join(__dirname, '../../../../../../lib/i18n/locales');
const LOCALES = readdirSync(LOCALE_DIR).filter((file) => file.endsWith('.json'));

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const WEBHOOK: ApprovalReason = {
  rule: 'external_policy',
  layer: 'org',
  sources: [],
  moreSources: 0,
  destination: '#sales',
  conversationGrant: true,
};

function approval(id: string, reason: ApprovalReason | undefined, extra: Partial<PendingApproval> = {}): PendingApproval {
  return {
    interactionId: id,
    toolCallId: `call-${id}`,
    toolName: 'slack__send_message',
    args: JSON.stringify({ channel: '#sales', text: 'Q3 numbers' }),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    ...(reason !== undefined ? { reason } : {}),
    ...extra,
  };
}

const catalogue = (file: string): { approvalCard: Record<string, unknown>; chat: { pastedSecret: Record<string, string> } } =>
  JSON.parse(readFileSync(join(LOCALE_DIR, file), 'utf8'));

describe('the policy webhook\'s card (WP-39, G-40)', () => {
  it('WP39-UI-1: the contract names the rule, and the card says the org\'s policy system asked', () => {
    expect(HOLD_RULES).toContain('external_policy');
    expect(approvalWhyKey(WEBHOOK)).toBe('approvalCard.externalPolicy.why');
    render(h(Theme, null, h(ApprovalCard, { approval: approval('ix.1', WEBHOOK), onDecide: vi.fn(), conversationChoice: true })));
    expect(screen.getByTestId('approval-why').textContent).toBe(
      "Your organization's policy system asked for a person to approve this. Nothing happens until you decide.",
    );
    expect(screen.queryByRole('button', { name: /for this conversation: / })).toBeNull();
    expect(screen.getByRole('button', { name: /^Allow once: / })).toBeTruthy();
    expect(offersConversation({ ...WEBHOOK, sensitivity: 'private' })).toBe(false);
  });

  it('WP39-UI-1: every locale words it', () => {
    expect(LOCALES).toHaveLength(10);
    for (const file of LOCALES) {
      const why = (catalogue(file).approvalCard.externalPolicy as { why?: string } | undefined)?.why;
      expect(why, file).toBeTruthy();
    }
  });

  it('WP39-UI-2: webhook asks of one batch are each their own card; other asks still group', () => {
    const asks = [1, 2, 3].map((n) => approval(`ix.${n}`, WEBHOOK, { afterTurns: 1, runId: 'run-1' }));
    expect(groupApprovals(asks).map((group) => group.items.length)).toEqual([1, 1, 1]);
    const plain = [1, 2].map((n) =>
      approval(`ix.${n}`, { ...WEBHOOK, rule: 'writes' }, { afterTurns: 1, runId: 'run-1' }),
    );
    expect(groupApprovals(plain).map((group) => group.items.length)).toEqual([2]);
  });
});

describe('an edit with a pasted secret (WP-39, SEC-CR-07 x AP-06)', () => {
  it('WP39-UI-3: the refusal tells the person to save it as a credential, in the composer\'s words', () => {
    expect(REFUSAL_KEYS[harnessHttpCode('EDIT_HAS_SECRET')]).toBe('approvalCard.editHasSecret');
    render(
      h(Theme, null, h(ApprovalCard, {
        approval: approval('ix.1', WEBHOOK, { status: 'failed', errorCode: harnessHttpCode('EDIT_HAS_SECRET') }),
        onDecide: vi.fn(),
      })),
    );
    expect(screen.getByText(/save it as a credential on your Actions page instead/)).toBeTruthy();
    for (const file of LOCALES) {
      const words = catalogue(file);
      const said = words.approvalCard.editHasSecret as string;
      expect(said, file).toBeTruthy();
      expect(said, file).toContain(words.chat.pastedSecret.credentialHint);
    }
  });
});
