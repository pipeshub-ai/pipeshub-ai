'use client';

import React, { useState } from 'react';
import { Box, Button, Card, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { ApprovalDecision, MessagePart, PendingApproval, VerificationCheck } from '../../types';
import { activeCardStyle, PausedLine, refusalKey, TARGET } from './approval-card';
import { DenyReasonForm } from './approval-effect';
import { isPlanApproval } from '../../interaction-wording';

const MAX_STEPS = 20;

export { isPlanApproval };

/** The plan the run submitted (`{plan, steps}`), or null when its arguments are not one: the card falls back. */
export function planOf(args: string): { plan: string; steps: string[] } | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(args);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== 'object') return null;
  const { plan, steps } = parsed as Record<string, unknown>;
  if (typeof plan !== 'string' || !plan.trim() || !Array.isArray(steps)) return null;
  const named = steps.filter((step): step is string => typeof step === 'string' && step.trim() !== '').slice(0, MAX_STEPS);
  return named.length > 0 ? { plan: plan.trim(), steps: named.map((step) => step.trim()) } : null;
}

export interface PlanApprovalCardProps {
  approval: PendingApproval;
  onDecide: (decision: Extract<ApprovalDecision, 'approved' | 'denied'>, reason?: string) => void;
  canApprove: boolean;
  canDeny: boolean;
  active?: boolean;
  paused?: boolean;
}

/**
 * G-04: the plan a run must have approved before it acts, as a plan: what it will do and its numbered steps. Approve
 * plan lets it act under its own rules (the steps become its plan list); Decline ends it with nothing done, and may
 * say why.
 */
export function PlanApprovalCard({ approval, onDecide, canApprove, canDeny, active = false, paused = false }: PlanApprovalCardProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [declining, setDeclining] = useState(false);
  const plan = planOf(approval.args);
  const sending = approval.status === 'sending';
  const refused = approval.errorCode ? refusalKey(approval.errorCode) ?? 'approvalCard.failed' : null;
  if (!plan) return null;
  const title = t('runGates.plan.title');
  return (
    <Card role="region" aria-label={title} data-testid="plan-approval-card" style={{ marginTop: 'var(--space-2)', ...activeCardStyle(active) }}>
      <Flex direction="column" gap="2">
        {paused ? <PausedLine /> : null}
        <Flex align="center" gap="2">
          <MaterialIcon name="checklist" size={18} color="var(--amber-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {title}
          </Text>
        </Flex>
        <Text size="2" color="gray">
          {t('runGates.plan.why')}
        </Text>
        <Text as="p" size="2" style={{ color: 'var(--gray-12)', margin: 0, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
          {plan.plan}
        </Text>
        <Box asChild style={{ margin: 0, paddingInlineStart: 'var(--space-5)' }}>
          <ol data-testid="plan-steps">
            {plan.steps.map((step, index) => (
              <li key={index}>
                <Text size="2" style={{ overflowWrap: 'anywhere' }}>
                  {step}
                </Text>
              </li>
            ))}
          </ol>
        </Box>
        {refused ? (
          <Text size="2" color="red" role="alert">
            {t(refused)}
          </Text>
        ) : null}
        {!canApprove && !canDeny ? (
          <Text size="2" color="gray">
            {t('approvalCard.noAuthority')}
          </Text>
        ) : declining ? (
          <DenyReasonForm
            disabled={sending}
            onCancel={() => setDeclining(false)}
            onDeny={(reason) => onDecide('denied', reason)}
            denyLabel={t('runGates.plan.decline')}
            denyText={t('runGates.plan.decline')}
          />
        ) : (
          <Flex gap="2" justify="end" wrap="wrap" direction={isMobile ? 'column-reverse' : 'row'}>
            {canDeny ? (
              <Button variant="soft" color="gray" disabled={sending} onClick={() => setDeclining(true)} style={TARGET}>
                {t('runGates.plan.declineMore')}
              </Button>
            ) : null}
            {canApprove ? (
              <Button disabled={sending} onClick={() => onDecide('approved')} style={TARGET}>
                {sending ? t('approvalCard.sending') : t('runGates.plan.approve')}
              </Button>
            ) : null}
          </Flex>
        )}
      </Flex>
    </Card>
  );
}

/** A decided plan's one line: approved, or declined with the person's reason. */
export function planReceipt(t: TFunction, card: Pick<PendingApproval, 'status' | 'denyReason'>): { tone: 'ok' | 'denied' | 'muted'; text: string; status: string } {
  if (card.status === 'approved') return { tone: 'ok', text: t('runGates.plan.receiptApproved'), status: 'approved' };
  if (card.status === 'denied') {
    return card.denyReason
      ? { tone: 'denied', text: t('runGates.plan.receiptDeclinedBecause', { reason: card.denyReason }), status: 'denied' }
      : { tone: 'denied', text: t('runGates.plan.receiptDeclined'), status: 'denied' };
  }
  return { tone: 'muted', text: t('interaction.receipt.lapsed'), status: card.status };
}

/** A check in words: its own reason when it failed, else what passed ("out/report.pdf opens", "The command pytest passed"). */
export function checkLine(t: TFunction, check: VerificationCheck): string {
  if (!check.passed && check.reason) return check.reason;
  const target = check.target ?? '';
  switch (check.kind) {
    case 'file':
      return check.passed ? t('runGates.verify.check.filePassed', { target }) : t('runGates.verify.check.fileFailed', { target });
    case 'json_schema':
      return check.passed ? t('runGates.verify.check.schemaPassed', { target }) : t('runGates.verify.check.schemaFailed', { target });
    case 'citations':
      return check.passed ? t('runGates.verify.check.citationsPassed') : t('runGates.verify.check.citationsFailed');
    case 'command':
      return check.passed ? t('runGates.verify.check.commandPassed', { target }) : t('runGates.verify.check.commandFailed', { target });
    default:
      return check.passed ? t('runGates.verify.check.otherPassed') : t('runGates.verify.check.otherFailed');
  }
}

/** The run's verification as the answer shows it, from its `verified` / `unverified` notice; null without checks. */
export function verificationOf(parts: readonly MessagePart[] | undefined): {
  verified: boolean;
  round: number;
  checks: VerificationCheck[];
} | null {
  let found: { verified: boolean; round: number; checks: VerificationCheck[] } | null = null;
  for (const part of parts ?? []) {
    if (part.type !== 'notice' || (part.code !== 'verified' && part.code !== 'unverified')) continue;
    const checks = Array.isArray(part.checks)
      ? part.checks.filter((check): check is VerificationCheck => typeof check?.kind === 'string' && typeof check.passed === 'boolean')
      : [];
    if (checks.length === 0) continue;
    found = { verified: part.code === 'verified', round: typeof part.round === 'number' && part.round > 0 ? part.round : 1, checks };
  }
  return found;
}

/**
 * G-05: under the answer, one row that says whether its checks passed (green) or not (red), "checked twice" when the
 * run was sent back to fix one; it opens to every check in plain words. Failed ones come first.
 */
export function VerificationPanel({ parts }: { parts: readonly MessagePart[] | undefined }) {
  const { t } = useTranslation();
  const verification = verificationOf(parts);
  const [open, setOpen] = useState(false);
  if (!verification) return null;
  const { verified, round, checks } = verification;
  const failed = checks.filter((check) => !check.passed).length;
  const summary = [
    verified
      ? t('runGates.verify.passedCount', { count: checks.length })
      : t('runGates.verify.failedCount', { failed, count: checks.length }),
    ...(round === 2 ? [t('runGates.verify.checkedTwice')] : round > 2 ? [t('runGates.verify.checkedTimes', { count: round })] : []),
  ].join(' · ');
  const ordered = [...checks].sort((a, b) => Number(a.passed) - Number(b.passed));
  const tone = verified ? 'jade' : 'red';
  return (
    <Box
      data-testid="verification-panel"
      data-status={verified ? 'verified' : 'unverified'}
      style={{
        marginTop: 'var(--space-3)',
        border: `1px solid var(--${tone}-6)`,
        backgroundColor: `var(--${tone}-2)`,
        borderRadius: 'var(--radius-3)',
      }}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        style={{
          all: 'unset',
          boxSizing: 'border-box',
          display: 'flex',
          width: '100%',
          minHeight: 44,
          alignItems: 'center',
          gap: 'var(--space-2)',
          padding: '0 var(--space-3)',
          cursor: 'pointer',
          flexWrap: 'wrap',
        }}
      >
        <MaterialIcon name={verified ? 'verified' : 'error_outline'} size={16} color={`var(--${tone}-11)`} />
        <Text size="2" weight="medium" style={{ color: `var(--${tone}-11)` }}>
          {verified ? t('runGates.verify.verified') : t('runGates.verify.unverified')}
        </Text>
        <Text size="1" color="gray" style={{ flex: 1 }}>
          {summary}
        </Text>
        <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={18} color="var(--gray-11)" />
      </button>
      {open ? (
        <Flex asChild direction="column" gap="1" style={{ margin: 0, padding: '0 var(--space-3) var(--space-3)', listStyle: 'none' }}>
          <ul data-testid="verification-checks">
            {ordered.map((check, index) => (
              <li key={index} data-passed={check.passed ? 'true' : 'false'}>
                <Flex gap="2" align="start">
                  <MaterialIcon name={check.passed ? 'check' : 'close'} size={16} color={check.passed ? 'var(--jade-11)' : 'var(--red-11)'} />
                  <Text size="2" style={{ overflowWrap: 'anywhere' }}>
                    {checkLine(t, check)}
                  </Text>
                </Flex>
              </li>
            ))}
          </ul>
        </Flex>
      ) : null}
    </Box>
  );
}
