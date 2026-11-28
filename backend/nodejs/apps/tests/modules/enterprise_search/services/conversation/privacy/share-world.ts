/**
 * Small hand-made chats for the share rules: rows stored as the save seals them, the people of a room and what each can
 * open, and the three ways a read is judged (closures made by the read, closures kept since the writes, and the walk a
 * read falls back to), so that each rule is tested on every one of them.
 */
import sinon from 'sinon'
import { Logger } from '../../../../../../src/libs/services/logger.service'
import {
  ON_BEHALF_OF_READER,
  configureVisibility,
  rememberAnswerClosures,
  resetVisibility,
  visibleRows,
  type Reader,
  type ReaderRow,
  type ReaderSession,
  type VisibilityDeps,
  type VisibleRowsOptions,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { memoryClosureStore } from './closure-world'

export const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
export const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
export const THREAD = 'dddddddddddddddddddddddd'
export const AGENT_THREAD = 'abababababababababababab'
export const BOBS_THREAD = 'bdbdbdbdbdbdbdbdbdbdbdbd'
export const FORK = 'cccccccccccccccccccccccc'

export const DANA = 'd00000000000000000000001'
export const CAROL = 'c00000000000000000000002'
export const BOB = 'b00000000000000000000003'
export const PRIYA = 'f00000000000000000000005'
export const ZED = '900000000000000000000006'

/** Carol and Dana can open R; nobody else can. Only Dana can open R2. */
export const R = 'rec-R'
export const R2 = 'rec-R2'
export const OPENS: Record<string, string[]> = { [DANA]: [R, R2], [CAROL]: [R], [BOB]: [], [PRIYA]: [], [ZED]: [] }

export const T0 = new Date('2026-10-01T10:00:00Z')
export const T1 = new Date('2026-10-02T10:00:00Z')

export const room: ReaderSession = {
  _id: ROOM,
  orgId: ORG,
  userId: DANA,
  sharedWith: [CAROL, BOB, PRIYA].map((userId) => ({ userId, accessLevel: 'write', addedAt: T0 })),
}
/** Dana's private thread of the room and the room's agent thread, as they are read: the room's people, their own id. */
export const privateThread: ReaderSession = { ...room, _id: THREAD, parentId: ROOM }
export const agentThread: ReaderSession = { ...room, _id: AGENT_THREAD, agentRoomId: ROOM, agentKey: 'X' }
/** Every chat as it is stored: a private thread is its asker's, and the room's owner owns the agent's thread of it. */
const STORED: ReaderSession[] = [
  room,
  { _id: THREAD, orgId: ORG, userId: DANA, parentId: ROOM },
  { _id: BOBS_THREAD, orgId: ORG, userId: BOB, parentId: ROOM },
  { _id: AGENT_THREAD, orgId: ORG, userId: DANA, agentRoomId: ROOM, agentKey: 'X' },
]

export type Row = ReaderRow & Record<string, unknown>

const DIRECT_KEYS = ['records', 'vrids', 'nodes', 'saVrids', 'personal', 'agents', 'chat', 'projects', 'marks']

let n = 0
/** Ids that read as ObjectIds made in the order they are asked for. */
export const nextId = (): string => {
  n += 1
  return `${(0x6abe3000 + n).toString(16)}${n.toString(16).padStart(16, '0')}`
}

/** An answer stored the way the save seals it: its label bound to its version and to the hash of its text. */
export const answer = (
  content: string,
  by: string,
  opts: { direct?: Record<string, unknown[]>; parents?: unknown[]; complete?: boolean; pub?: boolean; tagset?: number } & Record<string, unknown> = {},
): Row => {
  const { direct, parents, complete, pub, tagset, ...fields } = opts
  const labelVer = nextId()
  const parts = [{ type: 'text', content }]
  return {
    _id: nextId(),
    sessionId: ROOM,
    orgId: ORG,
    messageType: 'bot_response',
    requestedBy: by,
    content,
    parts,
    labelVer,
    createdAt: T1,
    provenance: {
      v: 5,
      ver: labelVer,
      contentHash: labelContentHash(content, parts),
      direct: { ...Object.fromEntries(DIRECT_KEYS.map((k) => [k, []])), ...direct },
      parents: parents ?? [],
      complete: complete ?? true,
      tagset: tagset ?? 2,
      pub: pub ?? false,
    },
    ...fields,
  }
}

/** A label's entry for a row its run was given: an answer at its version now, or a copy. */
export const given = (row: Row, mode = 'text'): { id: string; ver: string; mode: string } => ({
  id: String(row._id),
  ver: typeof row.labelVer === 'string' ? row.labelVer : 'legacy',
  mode,
})

export const refOf = (row: Row): { row: string; ver: string } => ({
  row: String(row._id),
  ver: row.messageType === 'shared_answer' ? '' : String(row.labelVer),
})

/** Shares the current version of an answer with everyone in its chat. */
export const share = (row: Row, more: Record<string, unknown> = {}): Row => {
  row.consent = { by: row.requestedBy, at: T1, ver: row.labelVer, scope: 'everyone', ...more }
  return row
}

export const stop = (row: Row): void => {
  delete row.consent
  delete row.consentTo
}

/** Writes a new version of an answer: the old one, its share with it, goes to the row's archive. */
export const regenerate = (row: Row, content: string, direct: Record<string, unknown[]> = {}): Row => {
  const old = { ver: row.labelVer, provenance: row.provenance, ...(row.consent !== undefined && { consent: row.consent }) }
  const next = answer(content, String(row.requestedBy), { direct })
  row.labelHistory = [...((row.labelHistory as unknown[] | undefined) ?? []), old]
  stop(row)
  row.labelVer = next.labelVer
  row.content = next.content
  row.parts = next.parts
  row.provenance = next.provenance
  return row
}

/** The message a private or agent thread's answer is shown to its room by. */
export const copyOf = (original: Row, more: Record<string, unknown> = {}): Row => ({
  _id: nextId(),
  sessionId: ROOM,
  orgId: ORG,
  messageType: 'shared_answer',
  authorUserId: original.requestedBy,
  requestedBy: original.requestedBy,
  content: String(original.content),
  createdAt: T1,
  sharedFrom: { sessionId: original.sessionId, messageId: original._id, ver: original.labelVer, sharedBy: original.requestedBy, sharedAt: T1, ...more },
})

/** Takes a copy out of its room, as a removal leaves it; `kept: false` as an older build did. */
export const takeOut = (copy: Row, opts: { kept?: boolean; again?: Row } = {}): Row => {
  const { sessionId, messageId, ver, sharedBy, sharedAt } = copy.sharedFrom as Record<string, unknown>
  copy.removedAt = T1
  copy.content = ''
  copy.sharedFrom = { sharedBy, sharedAt }
  if (opts.kept !== false) copy.removedFrom = { sessionId, messageId, ver }
  if (opts.again) copy.again = opts.again._id
  return copy
}

export interface World {
  deps: Partial<VisibilityDeps>
  store: Map<string, Row>
  /** The chats read by their id. */
  chatsAsked: string[]
  /** Who each agent question was asked for, and as whom. */
  agentsAsked: Array<{ userId: string; key: string; onBehalf: boolean }>
  serviceAsked: Array<{ userId: string; orgId: string; key: string }>
  /** Of which agents it was asked whether someone can't open a skill of theirs, and as whom. */
  skillsAsked: Array<{ userId: string; orgId: string; key: string; onBehalf: boolean }>
  /** Of which agents it was asked whether someone owns them, and as whom. */
  ownersAsked: Array<{ userId: string; key: string; onBehalf: boolean }>
  asked: Array<{ userId: string; ids: string[] }>
}

export interface WorldOptions {
  /** Who can use each agent; an agent left out can't be asked about. */
  agents?: Record<string, string[]>
  /** Who owns each agent; 'unknown' can't be read, one left out is nobody's here. */
  owners?: Record<string, string[] | 'unknown'>
  /** What each agent's record says of a service account; 'unknown' can't be read, one left out is none. */
  service?: Record<string, boolean | 'unknown'>
  /** Who can't open some skill each agent has now; 'unknown' can't be read, one left out has no skill. */
  closedSkills?: Record<string, string[] | 'unknown'>
  /** Who can read each chat other than those of the read; of one left out it can't be said. */
  chats?: Record<string, string[]>
}

/** The access service, the agents and the store over `rows`. */
export const world = (rows: Row[], opts: WorldOptions = {}): World => {
  const store = new Map(rows.map((r) => [String(r._id), r]))
  const agentsAsked: World['agentsAsked'] = []
  const serviceAsked: World['serviceAsked'] = []
  const skillsAsked: World['skillsAsked'] = []
  const ownersAsked: World['ownersAsked'] = []
  const asked: World['asked'] = []
  const chatsAsked: string[] = []
  const deps: Partial<VisibilityDeps> = {
    readable: async (who, ids) => {
      asked.push({ userId: who.userId, ids: [...ids] })
      const opens = new Set(OPENS[who.userId] ?? [])
      return { readable: new Set(ids.filter((id) => opens.has(id))), asked: new Set(ids), deleted: new Set<string>(), failed: false, validUntil: Date.now() + 30_000 }
    },
    labels: async (_org, ids) => new Map(ids.flatMap((id) => (store.has(id) ? [[id, store.get(id) as Row] as const] : []))),
    // As the repository reads them: a row a run wrote at its current version, or at one it archived.
    runs: async (_org, sessions, runIds) =>
      [...store.values()].filter(
        (row) =>
          sessions.includes(String(row.sessionId)) &&
          (runIds.includes(String(row.runId)) ||
            ((row.labelHistory as Array<{ runId?: unknown }> | undefined) ?? []).some((entry) => runIds.includes(String(entry.runId)))),
      ),
    canUseAgent: async (who, key) => {
      agentsAsked.push({ userId: who.userId, key, onBehalf: who.identity === ON_BEHALF_OF_READER })
      const users = opts.agents?.[key]
      return users === undefined ? undefined : users.includes(who.userId)
    },
    isServiceAgent: async (who, key) => {
      serviceAsked.push({ userId: who.userId, orgId: who.orgId, key })
      const record = opts.service?.[key] ?? false
      return record === 'unknown' ? undefined : record
    },
    hasClosedSkill: async (who, key) => {
      skillsAsked.push({ userId: who.userId, orgId: who.orgId, key, onBehalf: who.identity === ON_BEHALF_OF_READER })
      const closed = opts.closedSkills?.[key] ?? []
      return closed === 'unknown' ? undefined : closed.includes(who.userId)
    },
    ownsAgent: async (who, key) => {
      ownersAsked.push({ userId: who.userId, key, onBehalf: who.identity === ON_BEHALF_OF_READER })
      const owners = opts.owners?.[key] ?? []
      return owners === 'unknown' ? undefined : owners.includes(who.userId)
    },
    canReadConversation: async (who, chat) => opts.chats?.[chat]?.includes(who.userId),
    canReadProject: async () => undefined,
    sharedText: (text) => text,
    switchAt: async () => T0.getTime(),
    labelRule: async () => true,
    audiences: async (_org, ids) => new Map(ids.map((id) => [id, 0])),
    chats: async (_org, ids) => {
      chatsAsked.push(...ids)
      return new Map(STORED.filter((chat) => ids.includes(String(chat._id))).map((chat) => [String(chat._id), chat]))
    },
    store: new VerdictStore(),
  }
  return { deps, store, chatsAsked, agentsAsked, serviceAsked, skillsAsked, ownersAsked, asked }
}

/** How a read comes to its verdicts: closures it makes, closures kept since the writes, or the walk it falls back to. */
export const WAYS = ['closures', 'kept closures', 'the walk'] as const
export type Way = (typeof WAYS)[number]

/** A read whose share marks can't be read judges by the walk; `met` counts that it was asked. */
export const unreadShares = () => {
  const met = { shares: 0 }
  return {
    met,
    closures: {
      get: async () => new Map(),
      put: async () => undefined,
      shares: async () => {
        met.shares += 1
        throw new Error('share marks not read')
      },
    },
  }
}

/** Each row's verdict for `who` reading `session`, by the way named. */
export const verdicts = async (
  way: Way,
  w: World,
  who: string | Reader,
  session: ReaderSession,
  rows: Row[],
  opts: VisibleRowsOptions = {},
): Promise<Map<string, string>> => {
  resetVisibility()
  let warn: sinon.SinonStub | undefined
  const reader: Reader = typeof who === 'string' ? { userId: who, orgId: ORG, identity: 'a request' } : who
  const read = async (page: Row[], deps: Partial<VisibilityDeps>): Promise<Map<string, string>> => {
    const out = await visibleRows(reader, session, page, { member: true, ...opts, deps: { ...w.deps, ...deps } })
    return new Map([...out].map(([id, v]) => [id, v.verdict]))
  }
  try {
    if (way === 'closures') return await read(rows, {})
    if (way === 'kept closures') {
      const closures = memoryClosureStore(w.store) as unknown as VisibilityDeps['closures']
      configureVisibility({ ...w.deps, closures })
      await rememberAnswerClosures(ORG, [...w.store.values()])
      // A new process: the closures come back from the store.
      resetVisibility()
      return await read(rows, { closures })
    }
    const { met, closures } = unreadShares()
    // A version that names another makes the read look at its chat's shares, so this read falls back to the walk.
    const below = answer('Probe below.', DANA, { pub: true })
    const probe = answer('Probe.', DANA, { parents: [given(below)] })
    for (const row of [below, probe]) w.store.set(String(row._id), row)
    warn = sinon.stub(Logger.prototype, 'warn')
    const out = await read([...rows, probe], { closures })
    if (met.shares === 0) throw new Error('the read did not fall back to the walk')
    return out
  } finally {
    warn?.restore()
    resetVisibility()
  }
}

/** One row's verdict (see `verdicts`). */
export const verdictOf = async (
  way: Way,
  w: World,
  who: string | Reader,
  session: ReaderSession,
  row: Row,
  opts: VisibleRowsOptions = {},
): Promise<string | undefined> => (await verdicts(way, w, who, session, [row], opts)).get(String(row._id))
