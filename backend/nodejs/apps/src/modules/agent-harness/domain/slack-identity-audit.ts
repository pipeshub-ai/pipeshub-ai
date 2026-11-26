import { canonicalDigest } from '../contracts/canonical-json';
import type { AuditEntry, AuditFields } from './audit';
import { principalRefFor } from './run-spec';

export const SLACK_LINKED_KIND = 'identity.slack_linked';
export const SLACK_UNLINKED_KIND = 'identity.slack_unlinked';

/**
 * Why a link ended (R38IDENT-FU-5): an admin removed it, or the bot found at its next use that the PipesHub account
 * can no longer act (removed, disabled or made a service account) and removed it itself.
 */
export type SlackUnlinkReason =
  'admin_removed' | 'account_removed' | 'account_disabled' | 'service_account';

/** Who acts in PipesHub as a Slack person changed (ADR-SLACKID-01). Ids only: never a token or a Slack name. */
export type SlackIdentityChange =
  | {
      type: typeof SLACK_LINKED_KIND;
      orgId: string;
      teamId: string;
      slackUserId: string;
      /** The person who approved the link, who is also the account it binds. */
      userId: string;
      /** The account the Slack person acted as until now, when the approval moved the link. */
      previousUserId?: string;
    }
  | {
      type: typeof SLACK_UNLINKED_KIND;
      orgId: string;
      teamId: string;
      slackUserId: string;
      /** The account the link bound. */
      userId: string;
      /** The admin who removed it; absent when PipesHub removed it at its next use. */
      actorId?: string;
      reason: SlackUnlinkReason;
    };

/** One Slack person's stream of the org's chain: its own, as `wsdl:` is for a session's downloads. */
export const slackIdentityStreamId = (
  teamId: string,
  slackUserId: string,
): string => `slackid:${teamId}:${slackUserId}`;

/** The chain entry of a link or unlink. `at` is the change's time in milliseconds, which orders the stream. */
export function slackIdentityAuditEntry(
  change: SlackIdentityChange,
  at: number,
): AuditEntry {
  const fields: AuditFields = {
    teamId: change.teamId,
    slackUserId: change.slackUserId,
    account: principalRefFor(change.userId),
  };
  if (change.type === SLACK_LINKED_KIND) {
    fields['by'] = principalRefFor(change.userId);
    const moved =
      change.previousUserId !== undefined &&
      change.previousUserId !== change.userId;
    fields['moved'] = moved;
    if (moved && change.previousUserId !== undefined)
      fields['previousAccount'] = principalRefFor(change.previousUserId);
  } else {
    if (change.actorId !== undefined)
      fields['by'] = principalRefFor(change.actorId);
    else fields['actor'] = 'system';
    fields['reason'] = change.reason;
  }
  return {
    kind: change.type,
    sessionId: null,
    streamId: slackIdentityStreamId(change.teamId, change.slackUserId),
    runId: null,
    journalSeq: at,
    writer: 'node',
    attemptId: null,
    fence: null,
    fields,
    // An absent optional id is left out, never digested as undefined.
    dataDigest: canonicalDigest(
      JSON.parse(JSON.stringify({ ...change, at })) as Record<string, unknown>,
    ),
  };
}
