import { atLeast, CanonicalRole } from './ladder';

export interface ChatAttachmentRow {
  /** Absent on legacy rows, which read as authored by the session owner. */
  authorUserId?: string;
  sessionOwnerId: string;
  filesShared?: boolean;
}

/** The user turn that started the run which produced an artifact. */
export interface ChatArtifactTurn {
  shareToolResults?: boolean;
}

export interface ChatArtifactKind {
  visibility?: string;
  isTemporary?: boolean;
}

/** Who a row counts as authored by: its stamped author, else the session owner. */
export const attachmentAuthorOf = (row: ChatAttachmentRow): string =>
  row.authorUserId ?? row.sessionOwnerId;

/**
 * H4. With the flag on the turn's author must have consented (`filesShared === true`);
 * a legacy row has no consent and denies. With it off the rule matches the retired
 * grant path, which shared whatever the session owner had attached.
 */
export function canReadChatAttachment(
  chatRole: CanonicalRole,
  row: ChatAttachmentRow,
  flagOn: boolean,
): boolean {
  if (!atLeast(chatRole, 'viewer')) {
    return false;
  }
  return flagOn
    ? row.filesShared === true
    : attachmentAuthorOf(row) === row.sessionOwnerId;
}

/**
 * H5. A null turn is a legacy artifact without a `runId`, or a run with no user turn;
 * with the flag on it denies. With the flag off the turn is irrelevant.
 */
export function canReadChatArtifact(
  chatRole: CanonicalRole,
  turn: ChatArtifactTurn | null,
  kind: ChatArtifactKind,
  flagOn: boolean,
): boolean {
  if (!atLeast(chatRole, 'viewer')) {
    return false;
  }
  if (kind.visibility === 'STAGING' || kind.isTemporary === true) {
    return false;
  }
  return flagOn ? turn?.shareToolResults === true : true;
}
