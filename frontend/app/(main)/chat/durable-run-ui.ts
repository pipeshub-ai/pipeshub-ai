'use client';

import { budgetRefusalKey } from './code-tasks';
import { approvalEffectOf, MAX_DENY_REASON_CHARS } from './approval-preview';
import { connectTargetOf } from './connect-cards';
import type { ApprovalPartStatus, MessagePart, PendingApproval, RunBudgetView } from './types';
import { TERMINAL_EXECUTION_STATUSES } from '@/lib/contracts/harness/generated/ext/pipeshub/enums';

const APPROVAL_STATUSES: ReadonlySet<string> = new Set<ApprovalPartStatus>([
  'pending',
  'approved',
  'denied',
  'closed',
  'expired',
]);

/** An `approval` part (or a timeline's approval) as the approval card shows it. */
export function approvalFromPart(part: MessagePart): PendingApproval | null {
  if (part.type !== 'approval' || !part.interactionId) return null;
  const status = APPROVAL_STATUSES.has(String(part.status)) ? (part.status as ApprovalPartStatus) : 'closed';
  const effect = approvalEffectOf(part.effect);
  if (part.kind === 'connect') {
    // G-12: a call that waited for its owner to connect an item; the saved part never names the instance.
    const connect = connectTargetOf(part.connect);
    if (!connect) return null;
    const { instanceId: _instance, ...target } = connect;
    return {
      interactionId: part.interactionId,
      toolCallId: part.toolCallId ?? part.interactionId,
      toolName: part.toolName ?? '',
      ...(part.displayName ? { displayName: part.displayName } : {}),
      args: '',
      argsSummary: null,
      kind: 'connect',
      connect: target,
      status,
      errorCode: null,
    };
  }
  return {
    interactionId: part.interactionId,
    toolCallId: part.toolCallId ?? part.interactionId,
    toolName: part.toolName ?? '',
    ...(part.displayName ? { displayName: part.displayName } : {}),
    args: part.args ?? '{}',
    argsSummary: part.argsSummary ?? null,
    ...(part.reason ? { reason: part.reason } : {}),
    ...(part.scope === 'conversation' ? { scope: part.scope } : {}),
    ...(effect ? { effect } : {}),
    ...(status === 'denied' && typeof part.denyReason === 'string' && part.denyReason.trim()
      ? { denyReason: part.denyReason.trim().slice(0, MAX_DENY_REASON_CHARS) }
      : {}),
    ...(status === 'approved' && typeof part.editedArgs === 'string' && part.editedArgs.length > 0
      ? { editedArgs: part.editedArgs }
      : {}),
    status,
    errorCode: null,
  };
}

/** The run ended: an approval nobody decided can no longer be decided. */
export function withRunEnded(approvals: PendingApproval[]): PendingApproval[] {
  return approvals.some((a) => a.status === 'pending' || a.status === 'sending')
    ? approvals.map((a) => (a.status === 'pending' || a.status === 'sending' ? { ...a, status: 'expired' } : a))
    : approvals;
}

export { budgetRefusalKey };

export const usd = (micros: number): string =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: micros > 0 && micros < 10_000 ? 4 : 2,
  }).format(micros / 1_000_000);

export const count = (value: number): string => new Intl.NumberFormat('en-US').format(value);

export interface SpendText {
  spent: string;
  cap: string | null;
  tokens: string;
  tokenCap: string | null;
}

/** A run's spend as text: committed against its own cap (null: no cap of its own). */
export function formatSpend(budget: RunBudgetView): SpendText {
  return {
    spent: usd(budget.committed.usdMicros),
    cap: budget.cap.usdMicros === null ? null : usd(budget.cap.usdMicros),
    tokens: count(budget.committed.tokens),
    tokenCap: budget.cap.tokens === null ? null : count(budget.cap.tokens),
  };
}

const TERMINAL: ReadonlySet<string> = new Set(TERMINAL_EXECUTION_STATUSES);

/** A run status (the run document's, as the timeline answers it) the child panel keeps refreshing for. */
export function runStillActive(status: string | undefined): boolean {
  return status !== undefined && !TERMINAL.has(status);
}
