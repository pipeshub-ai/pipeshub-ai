import { readFileSync } from 'node:fs';
import path from 'node:path';
import { withApprovalRequested, withApprovalResolved } from '@/chat/approvals';
import type { PendingApproval, SSEApprovalRequestedEvent } from '@/chat/types';
import { REPO } from './fixtures';

/** The contract's examples of every frame the durable harness sends the chat (UX-01). */
const VECTORS = JSON.parse(readFileSync(path.join(REPO, 'contracts/harness/vectors/ui-stream-events.json'), 'utf8')) as {
  chat: { custom: Record<string, { example: { value: Record<string, unknown> } }> };
};
export const vector = (name: string): Record<string, unknown> => VECTORS.chat.custom[name].example.value;

/** A card as the stream callbacks keep it, from the contract's own example plus what a case adds. */
export function asked(extra: Partial<SSEApprovalRequestedEvent> = {}, list: PendingApproval[] = []): PendingApproval[] {
  return withApprovalRequested(list, { ...(vector('approval_requested') as unknown as SSEApprovalRequestedEvent), ...extra, runId: 'run-1' });
}
export const resolved = (list: PendingApproval[], interactionId: string, decision: string, scope?: 'conversation') =>
  withApprovalResolved(list, { interactionId, decision: decision as never, runId: 'run-1', ...(scope ? { scope } : {}) });
