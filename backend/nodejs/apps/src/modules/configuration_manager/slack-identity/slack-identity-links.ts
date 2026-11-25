import { createHash, randomBytes } from 'node:crypto';
import type { SlackUserLinkRepository } from '../../../libs/repositories/slack-user-link';
import { Logger } from '../../../libs/services/logger.service';
import type { SlackUnlinkReason } from '../../agent-harness/domain/slack-identity-audit';
import {
  type SlackIdentityAudit,
  unauditedSlackIdentity,
} from './slack-identity-audit';

/**
 * Who a Slack person is in PipesHub (ADR-SLACKID-01). A Slack team id and user id are bound to one PipesHub
 * account only when that person signs in to PipesHub and approves the link. Nothing a Slack profile carries (its
 * email above all, which a Slack admin of another organisation can set to anything) ever picks the account.
 */

/** The KV operations the links need; `KeyValueStoreService` has them. */
export interface SlackLinkKv {
  get<T>(key: string): Promise<T | null>;
  set(key: string, value: unknown): Promise<void>;
  delete(key: string): Promise<void>;
  compareAndSet<T>(
    key: string,
    expectedValue: T | null,
    newValue: T,
  ): Promise<boolean>;
  listKeysInDirectory(directory: string): Promise<string[]>;
}

export interface SlackIdentity {
  teamId: string;
  slackUserId: string;
}

export interface SlackIdentityLink extends SlackIdentity {
  orgId: string;
  userId: string;
  slackName?: string;
  linkedAt: string;
}

export interface SlackLinkRequest extends SlackIdentity {
  /** The organisation whose bot asked: only a person of that organisation can complete the link. */
  orgId: string;
  configId: string;
  botName?: string;
  slackName?: string;
  expiresAt: number;
}

interface StoredLinkRequest extends SlackLinkRequest {
  used: boolean;
}

/** Why a link is removed: by an admin (named), or by PipesHub at its next use because its account cannot act. */
export type SlackUnlinkCause =
  | { by: 'admin'; adminId: string }
  | { by: 'system'; reason: Exclude<SlackUnlinkReason, 'admin_removed'> };

export const SLACK_LINK_REQUEST_TTL_MS = 10 * 60 * 1000;

/** Slack's ids are upper-case letters and digits. Anything else (case or Unicode look-alikes, separators that would
 * move a KV key) is not a Slack id, so it never names a link. */
const SLACK_ID = /^[A-Z0-9]{2,32}$/;
const LINK_TOKEN = /^[A-Za-z0-9_-]{43}$/;

const ROOT = '/services/slackIdentity';
const requestKey = (hash: string): string => `${ROOT}/requests/${hash}`;
const latestRequestKey = (orgId: string, id: SlackIdentity): string =>
  `${ROOT}/latestRequest/${orgId}/${id.teamId}/${id.slackUserId}`;
const tokenHash = (token: string): string =>
  createHash('sha256').update(token, 'utf8').digest('hex');

export type SlackLinkRequestProblem =
  'unknown' | 'expired' | 'used' | 'other_org';

export class SlackLinkRequestError extends Error {
  constructor(readonly problem: SlackLinkRequestProblem) {
    super(SLACK_LINK_REQUEST_MESSAGES[problem]);
    this.name = 'SlackLinkRequestError';
  }
}

export const SLACK_LINK_REQUEST_MESSAGES: Record<
  SlackLinkRequestProblem,
  string
> = {
  unknown:
    'This link is not valid. Send the PipesHub bot a message in Slack to get a new one.',
  expired:
    'This link has expired. Send the PipesHub bot a message in Slack to get a new one.',
  used: 'This link was already used. Send the PipesHub bot a message in Slack if you need a new one.',
  other_org:
    'This link is for another organization. Sign in to the PipesHub account of the organization whose Slack bot sent it.',
};

export function isSlackId(value: unknown): value is string {
  return typeof value === 'string' && SLACK_ID.test(value);
}

export function isSlackIdentity(
  value: Partial<SlackIdentity>,
): value is SlackIdentity {
  return isSlackId(value.teamId) && isSlackId(value.slackUserId);
}

function parseRequest(raw: unknown): StoredLinkRequest | null {
  if (typeof raw !== 'string') return null;
  try {
    return JSON.parse(raw) as StoredLinkRequest;
  } catch {
    return null;
  }
}

function boundedText(value: unknown, max = 120): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim().slice(0, max);
  return trimmed === '' ? undefined : trimmed;
}

/**
 * The links themselves are their organisation's own rows (`slack-user-link`, keyed by organisation, workspace and
 * Slack user); the single-use link requests are short-lived KV entries.
 */
export class SlackIdentityLinks {
  constructor(
    private readonly kv: SlackLinkKv,
    private readonly store: SlackUserLinkRepository,
    private readonly now: () => number = Date.now,
    private readonly audit: SlackIdentityAudit = unauditedSlackIdentity,
  ) {}

  async find(
    orgId: string,
    identity: SlackIdentity,
  ): Promise<SlackIdentityLink | null> {
    if (!isSlackIdentity(identity)) return null;
    const row = await this.store.get(
      orgId,
      identity.teamId,
      identity.slackUserId,
    );
    // A stored row must still name the identity and organisation it is filed under.
    if (
      !row ||
      row.orgId !== orgId ||
      row.teamId !== identity.teamId ||
      row.slackUserId !== identity.slackUserId
    ) {
      return null;
    }
    return asLink(row);
  }

  /** A fresh single-use link for this Slack person in this organisation; the one sent before stops working.
   * Returns the secret token, which is stored only as its hash. */
  async request(
    input: SlackLinkRequest | Omit<SlackLinkRequest, 'expiresAt'>,
  ): Promise<{ token: string; expiresAt: number }> {
    if (
      !isSlackIdentity(input) ||
      typeof input.orgId !== 'string' ||
      input.orgId === ''
    ) {
      throw new SlackLinkRequestError('unknown');
    }
    const token = randomBytes(32).toString('base64url');
    const hash = tokenHash(token);
    const expiresAt = this.now() + SLACK_LINK_REQUEST_TTL_MS;
    const record: StoredLinkRequest = {
      orgId: input.orgId,
      teamId: input.teamId,
      slackUserId: input.slackUserId,
      configId: input.configId,
      botName: boundedText(input.botName),
      slackName: boundedText(input.slackName),
      expiresAt,
      used: false,
    };
    const latest = latestRequestKey(input.orgId, input);
    const previous = await this.kv.get<string>(latest);
    await this.kv.set(requestKey(hash), JSON.stringify(record));
    await this.kv.set(latest, hash);
    if (typeof previous === 'string' && previous !== hash)
      await this.kv.delete(requestKey(previous));
    return { token, expiresAt };
  }

  /** The request a link names, for a person of `orgId`; another organisation's link is refused (`other_org`). */
  async inspect(token: string, orgId: string): Promise<SlackLinkRequest> {
    const { record } = await this.open(token, orgId);
    const { used: _used, ...request } = record;
    return request;
  }

  /** Binds the Slack person to this PipesHub account. Spends the link first, atomically, so two tabs (or a replayed
   * request) cannot both complete it. */
  async complete(
    token: string,
    account: { orgId: string; userId: string; email: string },
  ): Promise<SlackIdentityLink> {
    const { hash, raw, record } = await this.open(token, account.orgId);
    await this.spend(hash, raw, record);
    const previous = await this.store.get(
      account.orgId,
      record.teamId,
      record.slackUserId,
    );
    const row = await this.store.upsert({
      orgId: account.orgId,
      workspaceId: record.teamId,
      teamId: record.teamId,
      slackUserId: record.slackUserId,
      userId: account.userId,
      email: account.email,
      slackName: record.slackName ?? '',
    });
    try {
      await this.audit({
        type: 'identity.slack_linked',
        orgId: account.orgId,
        teamId: record.teamId,
        slackUserId: record.slackUserId,
        userId: account.userId,
        ...(previous ? { previousUserId: previous.userId } : {}),
      });
    } catch (error) {
      // A link nobody can see on the audit chain is not kept: the Slack person goes back to how they were.
      if (previous) {
        const { linkedAt: _linkedAt, ...restored } = previous;
        await this.store.upsert(restored);
      } else
        await this.store.remove(
          account.orgId,
          record.teamId,
          record.slackUserId,
        );
      throw error;
    }
    await this.forget(hash, record);
    return asLink(row);
  }

  async decline(token: string, orgId: string): Promise<void> {
    const { hash, raw, record } = await this.open(token, orgId);
    await this.spend(hash, raw, record);
    await this.forget(hash, record);
  }

  async revoke(
    orgId: string,
    identity: SlackIdentity,
    cause: SlackUnlinkCause,
  ): Promise<boolean> {
    if (!isSlackIdentity(identity)) return false;
    const existing = await this.store.get(
      orgId,
      identity.teamId,
      identity.slackUserId,
    );
    if (!existing) return false;
    await this.store.remove(orgId, identity.teamId, identity.slackUserId);
    try {
      await this.audit({
        type: 'identity.slack_unlinked',
        orgId,
        teamId: identity.teamId,
        slackUserId: identity.slackUserId,
        userId: existing.userId,
        ...(cause.by === 'admin'
          ? { actorId: cause.adminId, reason: 'admin_removed' as const }
          : { reason: cause.reason }),
      });
    } catch (error) {
      // Taking authority away never waits on the audit store: the link stays removed and the gap is logged.
      Logger.getInstance({ service: 'SlackIdentityLinks' }).error(
        'slack_identity.unlink_not_audited',
        {
          reason: cause.by === 'admin' ? 'admin_removed' : cause.reason,
          errorType: error instanceof Error ? error.name : typeof error,
        },
      );
    }
    return true;
  }

  async list(orgId: string): Promise<SlackIdentityLink[]> {
    const rows = await this.store.listForOrg(orgId);
    return rows
      .map(asLink)
      .sort((a, b) => a.linkedAt.localeCompare(b.linkedAt));
  }

  private async open(
    token: string,
    orgId: string,
  ): Promise<{ hash: string; raw: string; record: StoredLinkRequest }> {
    if (typeof token !== 'string' || !LINK_TOKEN.test(token))
      throw new SlackLinkRequestError('unknown');
    const hash = tokenHash(token);
    const raw = await this.kv.get<string>(requestKey(hash));
    const record = parseRequest(raw);
    // A request from before requests named their organisation is not valid: the person asks for a new one.
    if (
      typeof raw !== 'string' ||
      !record ||
      !isSlackIdentity(record) ||
      typeof record.orgId !== 'string'
    ) {
      throw new SlackLinkRequestError('unknown');
    }
    if (record.orgId !== orgId) throw new SlackLinkRequestError('other_org');
    if (record.used) throw new SlackLinkRequestError('used');
    if (!(record.expiresAt > this.now()))
      throw new SlackLinkRequestError('expired');
    return { hash, raw, record };
  }

  private async spend(
    hash: string,
    raw: string,
    record: StoredLinkRequest,
  ): Promise<void> {
    const spent = await this.kv.compareAndSet<string>(
      requestKey(hash),
      raw,
      JSON.stringify({ ...record, used: true }),
    );
    if (!spent) throw new SlackLinkRequestError('used');
  }

  private async forget(
    hash: string,
    request: StoredLinkRequest,
  ): Promise<void> {
    const latest = latestRequestKey(request.orgId, request);
    if ((await this.kv.get<string>(latest)) === hash) {
      await this.kv.delete(latest);
    }
    await this.kv.delete(requestKey(hash));
  }
}

function asLink(row: {
  orgId: string;
  teamId: string;
  slackUserId: string;
  userId: string;
  slackName: string;
  linkedAt: Date;
}): SlackIdentityLink {
  return {
    orgId: row.orgId,
    teamId: row.teamId,
    slackUserId: row.slackUserId,
    userId: row.userId,
    ...(row.slackName ? { slackName: row.slackName } : {}),
    linkedAt: row.linkedAt.toISOString(),
  };
}
