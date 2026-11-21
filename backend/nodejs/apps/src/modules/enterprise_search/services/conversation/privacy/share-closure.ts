import { labelStateOf } from './label';
import {
  LABEL_DIRECT_KEYS,
  LABEL_SCHEMA_VERSION,
  type AnswerProvenance,
  type LabelHistoryEntry,
  type LabelParent,
} from './label.types';

/** Who a share would reach: every member of the chat, the people named, or someone outside the chat (a file grant). */
export type ShareAudience = 'everyone' | { userIds: string[] } | 'outside';

export type ShareRefusalCode =
  'SHARE_NOT_WIDENABLE' | 'SHARE_NOT_ALLOWED_AGENT';

export interface WidenResult {
  ok: boolean;
  code?: ShareRefusalCode;
  /** Members the share would show the text to who can't open what it used, when the check worked them out. */
  cannotOpen?: string[];
}

/** A consent stored on an answer row: who shared which version, with everyone or with the people in `consentTo`. */
export interface StoredConsent {
  by: unknown;
  at: Date | string;
  ver: string;
  scope: 'everyone' | 'people';
  /** 'earlier': the owner's one-step consent to show answers written before labels. */
  how?: string;
}

export interface ConsentEntry {
  userId: unknown;
  /** Which membership of the person the share was for: a removal and re-add starts another one. */
  memberSince: string;
}

/** A row as the share check reads it, label fields included. */
export interface ShareableRow {
  _id: unknown;
  sessionId?: unknown;
  orgId?: unknown;
  messageType?: unknown;
  requestedBy?: unknown;
  respondingAgentKey?: unknown;
  labelVer?: unknown;
  provenance?: unknown;
  labelHistory?: unknown;
  content?: unknown;
  parts?: unknown;
  consent?: StoredConsent | null;
  consentTo?: ConsentEntry[] | null;
  removedAt?: unknown;
  status?: unknown;
}

/**
 * A reader as the consent check sees them: `member` when they read the chat now, and `memberSince` the membership
 * their own share row records (`owner` or `direct:<when it was added>`), absent when none does.
 */
export interface ConsentViewer {
  userId: string;
  member?: boolean;
  memberSince?: string;
}

export interface ShareClosureDeps {
  /** Which of `ids` (records and containers) the person can open now, asked fresh; null when the check failed. */
  readable(
    orgId: string,
    userId: string,
    ids: string[],
  ): Promise<ReadonlySet<string> | null>;
  /** A row of the org by id, with its label fields, removed or not; null when there is none. */
  row(orgId: string, id: string): Promise<ShareableRow | null>;
  /** The agent of the chat that holds a row; undefined for none, null when it could not be read. */
  sessionAgent(
    orgId: string,
    sessionId: string,
  ): Promise<string | undefined | null>;
  /** Whether the agent runs as a service account, asked as `userId`; null when that could not be read. */
  agentIsService(
    orgId: string,
    userId: string,
    agentKey: string,
  ): Promise<boolean | null>;
  /** Whether the person can use the agent; null when that could not be read. */
  canUseAgent(
    orgId: string,
    userId: string,
    agentKey: string,
  ): Promise<boolean | null>;
}

/** The most rows one check reads; a longer chain is never judged partly, so it can't be shared or shown. */
export const SHARE_CLOSURE_MAX_ROWS = 200;

let configured: ShareClosureDeps | undefined;

/** What the checks below read through in this process; bound once at start. */
export const useShareClosureDeps = (
  deps: ShareClosureDeps | undefined,
): void => {
  configured = deps;
};

export const shareClosureDeps = (): ShareClosureDeps | undefined => configured;

const isPlain = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v);

/** A stored id (a string or an ObjectId) as text; '' for none. */
const idOf = (v: unknown): string =>
  v == null
    ? ''
    : typeof v === 'string'
      ? v
      : (v as { toString(): string }).toString();

type ArchivedVersion = LabelHistoryEntry & {
  consent?: StoredConsent;
  consentTo?: ConsentEntry[];
};

interface Version {
  label: AnswerProvenance | undefined;
  consent: StoredConsent | null;
  consentTo: ConsentEntry[];
  /** The row's current version, not one from its archive. */
  current: boolean;
}

/** Row `row` at version `ver`: its label and what it was shared as then, from the row or its archive. */
export function versionOf(row: ShareableRow, ver: string): Version | undefined {
  if (idOf(row.labelVer) === ver) {
    const consent = row.consent?.ver === ver ? row.consent : null;
    return {
      label: row.provenance as AnswerProvenance | undefined,
      consent,
      consentTo: consent && Array.isArray(row.consentTo) ? row.consentTo : [],
      current: true,
    };
  }
  const history = Array.isArray(row.labelHistory)
    ? (row.labelHistory as Array<ArchivedVersion | null | undefined>)
    : [];
  const entry = history.find((e) => e?.ver === ver);
  if (!entry) return undefined;
  const consent = entry.consent?.ver === ver ? entry.consent : null;
  return {
    label: entry.provenance,
    consent,
    consentTo: consent && Array.isArray(entry.consentTo) ? entry.consentTo : [],
    current: false,
  };
}

/** A label a reader may rely on without its hash: this schema, every list well formed, and complete. */
const usableLabel = (label: unknown): label is AnswerProvenance => {
  if (!isPlain(label) || label.v !== LABEL_SCHEMA_VERSION) return false;
  if (label.complete !== true) return false;
  if (Array.isArray(label.incomplete) && label.incomplete.length > 0)
    return false;
  const direct = label.direct;
  if (!isPlain(direct)) return false;
  if (
    Object.keys(direct).some(
      (k) => !(LABEL_DIRECT_KEYS as readonly string[]).includes(k),
    )
  ) {
    return false;
  }
  return (
    LABEL_DIRECT_KEYS.every((k) => Array.isArray(direct[k])) &&
    Array.isArray(label.parents)
  );
};

interface Node {
  row: ShareableRow;
  label: AnswerProvenance;
  version: Version;
  parents: LabelParent[];
}

class Refused extends Error {
  constructor(readonly code: ShareRefusalCode) {
    super(code);
  }
}

// A declaration, not an arrow: a call to it then ends the path for the type checker too.
function refuse(code: ShareRefusalCode = 'SHARE_NOT_WIDENABLE'): never {
  throw new Refused(code);
}

/** How a chain is judged: by whose access, and what may stand in for a parent the person can't pass. */
interface Judgement {
  who: string;
  /** A parent version the person can't pass by their own access is still covered by this. */
  covered(version: Version): boolean;
  /** An agent's configuration may be shown. */
  configShown(agentKey: string): Promise<boolean>;
}

/**
 * Whether the asker of `row` may show version `ver` of it to `audience`: the answer and every
 * answer it built on, however many follow-ups back, judged one by one by the asker's own access, asked fresh.
 *
 * Refused (`SHARE_NOT_WIDENABLE`) when any row of that chain has a label that is missing, stale, incomplete or of an
 * unknown version; names a record or container the asker can't open now, someone else's personal tool, a project, or
 * an upload from another chat; or builds on a file, a grant, a pre-switch view, or an answer shared with people. A
 * parent answer shared with everyone satisfies a share inside the chat, never one outside it. Refused
 * (`SHARE_NOT_ALLOWED_AGENT`) when any row of the chain came from a service-account agent, searched as a service
 * identity, or used an agent's configuration the audience may not be shown. Anything that can't be read refuses.
 */
export async function widenCheck(
  deps: ShareClosureDeps,
  row: ShareableRow,
  ver: string,
  audience: ShareAudience,
): Promise<WidenResult> {
  const orgId = idOf(row.orgId);
  const inChat = audience !== 'outside';
  const judgement: Judgement = {
    who: idOf(row.requestedBy),
    covered: (version) => inChat && version.consent?.scope === 'everyone',
    configShown: async (key) => {
      if (audience === 'outside' || audience === 'everyone') return false;
      for (const userId of audience.userIds) {
        if ((await deps.canUseAgent(orgId, userId, key)) !== true) return false;
      }
      return true;
    },
  };
  try {
    if (
      row.messageType !== 'bot_response' ||
      row.removedAt != null ||
      row.status === 'stopped'
    ) {
      refuse();
    }
    if (idOf(row.labelVer) !== ver || labelStateOf(row) !== 'ok') refuse();
    await judge(deps, row, ver, judgement);
    return { ok: true };
  } catch (error) {
    if (error instanceof Refused) return { ok: false, code: error.code };
    return { ok: false, code: 'SHARE_NOT_WIDENABLE' };
  }
}

/**
 * Whether `viewer` can open everything version `ver` of `row` was given: by their own access, the
 * personal tools of nobody else, every agent rule, and every parent at its version passing for them too or shared
 * with them. False on anything that can't be read.
 */
export async function fullCheck(
  deps: ShareClosureDeps,
  viewer: ConsentViewer & { orgId?: string },
  row: ShareableRow,
  ver: string,
): Promise<boolean> {
  const orgId = idOf(row.orgId);
  const judgement: Judgement = {
    who: viewer.userId,
    covered: (version) => consentCoversVersion(version, viewer),
    configShown: async (key) =>
      (await deps.canUseAgent(orgId, viewer.userId, key)) === true,
  };
  try {
    if (idOf(row.labelVer) === ver && labelStateOf(row) !== 'ok') return false;
    await judge(deps, row, ver, judgement);
    return true;
  } catch {
    return false;
  }
}

async function judge(
  deps: ShareClosureDeps,
  row: ShareableRow,
  ver: string,
  judgement: Judgement,
): Promise<void> {
  const orgId = idOf(row.orgId);
  if (!orgId || !judgement.who) refuse();
  const nodes = await closureOf(deps, orgId, row, ver);
  for (const key of await agentConfigsOf(deps, orgId, judgement.who, nodes)) {
    if (!(await judgement.configShown(key))) refuse('SHARE_NOT_ALLOWED_AGENT');
  }
  const ids = new Set<string>();
  for (const node of nodes.values()) {
    for (const id of [...node.label.direct.records, ...node.label.direct.nodes])
      ids.add(id);
  }
  const readable =
    ids.size === 0
      ? new Set<string>()
      : await deps.readable(orgId, judgement.who, [...ids]);
  if (readable === null) refuse();
  const opens = readable;

  const passes = (key: string, seen: ReadonlySet<string>): boolean => {
    const node = nodes.get(key);
    if (!node || seen.has(key)) return false;
    const { direct } = node.label;
    if ([...direct.records, ...direct.nodes].some((id) => !opens.has(id)))
      return false;
    if (direct.personal.some((p) => p !== judgement.who)) return false;
    if (direct.projects.length > 0) return false;
    if (direct.chat.some((c) => c.conversationId !== idOf(node.row.sessionId)))
      return false;
    const next = new Set(seen).add(key);
    return node.parents.every((p) => {
      const parentKey = `${p.id}@${p.ver}`;
      const parent = nodes.get(parentKey);
      if (!parent) return false;
      return judgement.covered(parent.version) || passes(parentKey, next);
    });
  };
  if (!passes(`${idOf(row._id)}@${ver}`, new Set())) refuse();
}

/** Every row version the answer built on, by `id@ver`, each with its label; refuses on anything unreadable. */
async function closureOf(
  deps: ShareClosureDeps,
  orgId: string,
  root: ShareableRow,
  ver: string,
): Promise<Map<string, Node>> {
  const nodes = new Map<string, Node>();
  const queue: Array<{ row: ShareableRow; ver: string }> = [{ row: root, ver }];
  for (let next = queue.shift(); next !== undefined; next = queue.shift()) {
    const { row, ver: at } = next;
    const key = `${idOf(row._id)}@${at}`;
    if (nodes.has(key)) continue;
    if (nodes.size >= SHARE_CLOSURE_MAX_ROWS) refuse();
    const version = versionOf(row, at);
    if (!version || !usableLabel(version.label)) refuse();
    const label = version.label;
    nodes.set(key, { row, label, version, parents: label.parents });
    for (const parent of label.parents) {
      // A file, a direct grant or a pre-switch view is someone's own access, never something to pass on.
      if (
        parent.mode === 'grant' ||
        parent.mode === 'preswitch' ||
        !parent.id ||
        parent.artifactId
      ) {
        refuse();
      }
      const parentRow = await deps.row(orgId, parent.id);
      if (!parentRow || idOf(parentRow.orgId) !== orgId) refuse();
      queue.push({ row: parentRow, ver: parent.ver });
    }
  }
  return nodes;
}

/**
 * The agent configurations the chain's labels name. Refuses on a service-account agent (named by a label, by a guest
 * agent's answer, or by the chat the row is in, so rows written before labels are covered too), on a
 * search as a service identity, and on an agent whose record can't be read.
 */
async function agentConfigsOf(
  deps: ShareClosureDeps,
  orgId: string,
  who: string,
  nodes: Map<string, Node>,
): Promise<Set<string>> {
  const config = new Set<string>();
  const keys = new Set<string>();
  const sessions = new Set<string>();
  for (const { row, label } of nodes.values()) {
    if (label.direct.saVrids.length > 0) refuse('SHARE_NOT_ALLOWED_AGENT');
    for (const agent of label.direct.agents) {
      if (agent.kind === 'service') refuse('SHARE_NOT_ALLOWED_AGENT');
      config.add(agent.key);
      keys.add(agent.key);
    }
    if (typeof row.respondingAgentKey === 'string' && row.respondingAgentKey) {
      keys.add(row.respondingAgentKey);
    }
    if (row.sessionId != null) sessions.add(idOf(row.sessionId));
  }
  for (const sessionId of sessions) {
    const key = await deps.sessionAgent(orgId, sessionId);
    if (key === null) refuse();
    if (key) keys.add(key);
  }
  for (const key of keys) {
    if ((await deps.agentIsService(orgId, who, key)) !== false)
      refuse('SHARE_NOT_ALLOWED_AGENT');
  }
  return config;
}

/** `versionOf` keeps only a consent made for that very version; a share with people covers the current one only. */
const consentCoversVersion = (
  version: Version,
  viewer: ConsentViewer,
): boolean => {
  const { consent } = version;
  if (!consent || viewer.member !== true) return false;
  switch (consent.scope) {
    case 'everyone':
      return true;
    case 'people':
      return (
        version.current &&
        viewer.memberSince !== undefined &&
        version.consentTo.some(
          (entry) =>
            idOf(entry.userId) === viewer.userId &&
            entry.memberSince === viewer.memberSince,
        )
      );
    default:
      // A stored scope this code does not know covers no one.
      return false;
  }
};

/**
 * Whether a consent stored on `row` covers `viewer` for version `ver`: a share with everyone covers
 * every member now and later; a share with people covers each of them only in the membership their own share row
 * recorded when it was made, for the version that was current then.
 */
export function consentCoversViewer(
  row: ShareableRow,
  ver: string,
  viewer: ConsentViewer,
): boolean {
  const version = versionOf(row, ver);
  return version !== undefined && consentCoversVersion(version, viewer);
}
