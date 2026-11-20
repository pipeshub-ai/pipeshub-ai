/**
 * Random chats for the closure property tests: rows of every kind the reader check judges, with labels that name
 * records, containers, people, agents, uploads, projects and marks, earlier answers in every mode, files by the run
 * that wrote them, versions archived by a regenerate or a removal, shares of current and archived versions, answers
 * from before labels, copies and questions (some removed), and labels that cannot be relied on. Every parent is an
 * earlier version, as the AI backend names them; a world is the same for a seed.
 */
import { archivedLabelHistory, labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import type { Reader, ReaderRow, ReaderSession, VisibilityDeps } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'

export const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
export const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
export const THREAD = 'dddddddddddddddddddddddd'
export const OTHER = 'eeeeeeeeeeeeeeeeeeeeeeee'

export const DANA = 'd00000000000000000000001'
export const CAROL = 'c00000000000000000000002'
export const BOB = 'b00000000000000000000003'
export const IVY = 'e00000000000000000000004'
export const PRIYA = 'f00000000000000000000005'
export const ZED = '900000000000000000000006'
export const PEOPLE = [DANA, CAROL, BOB, IVY, PRIYA, ZED]

export const T0 = new Date('2026-10-01T10:00:00Z')
export const T1 = new Date('2026-10-02T10:00:00Z')
export const T2 = new Date('2026-10-03T10:00:00Z')

const RECORDS = ['rec-a', 'rec-b', 'rec-c', 'rec-d', 'rec-e']
const NODES = ['kb-1', 'kb-2']
const AGENTS = ['agent-open', 'agent-closed', 'agent-unknown']
const PROJECTS = ['proj-open', 'proj-closed', 'proj-unknown']
const DIRECT_KEYS = ['records', 'vrids', 'nodes', 'saVrids', 'personal', 'agents', 'chat', 'projects', 'marks']

/** A small seeded generator (mulberry32): the same world for the same seed. */
export class Rng {
  private state: number
  constructor(seed: number) {
    this.state = seed >>> 0
  }
  next(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0
    let t = this.state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  chance(p: number): boolean {
    return this.next() < p
  }
  int(n: number): number {
    return Math.floor(this.next() * n)
  }
  pick<T>(items: readonly T[]): T {
    return items[this.int(items.length)] as T
  }
  some<T>(items: readonly T[], p: number): T[] {
    return items.filter(() => this.chance(p))
  }
}

export type Row = ReaderRow & Record<string, unknown>

export interface World {
  rows: Row[]
  store: Map<string, Row>
  sessions: Map<string, ReaderSession>
  opens: Map<string, Set<string>>
  deleted: Set<string>
  agents: Map<string, Set<string> | undefined>
  chatsRead: Map<string, Map<string, boolean | undefined>>
  projectsRead: Map<string, Map<string, boolean | undefined>>
  /** The access check fails for these readers. */
  failing: Set<string>
  switchAt: number | undefined | 'fails'
  /** Each row as it was after each write of it (`write`: a version written) or other change, in the order they happened. */
  timeline: Array<{ kind: 'write' | 'change'; row: Row }>
}

let counter = 0
/** Ids that sort and read as ObjectIds, made in time order. */
const nextId = (at: Date): string => {
  counter += 1
  const seconds = Math.floor(at.getTime() / 1000) + counter
  return `${seconds.toString(16).padStart(8, '0')}${counter.toString(16).padStart(16, '0')}`
}

const emptyDirect = (): Record<string, unknown[]> => Object.fromEntries(DIRECT_KEYS.map((k) => [k, []]))

/** An earlier version a label may name: a row and one of its versions, real or not. */
interface Named {
  row: Row
  vers: string[]
}

export function makeWorld(seed: number): World {
  counter = 0
  const rng = new Rng(seed)
  const room: ReaderSession = {
    _id: ROOM,
    orgId: ORG,
    userId: DANA,
    sharedWith: [
      { userId: CAROL, accessLevel: 'write', addedAt: T0 },
      { userId: BOB, accessLevel: 'write', addedAt: T0 },
      { userId: IVY, accessLevel: 'read', addedAt: rng.chance(0.5) ? T0 : T2 },
      { userId: PRIYA, accessLevel: 'write', addedAt: T2 },
    ],
    ...(rng.chance(0.25) && { privacyStampedAt: new Date('2026-10-02T12:00:00Z') }),
  }
  const thread: ReaderSession = { _id: THREAD, orgId: ORG, userId: CAROL, parentId: ROOM }
  const other: ReaderSession = { _id: OTHER, orgId: ORG, userId: BOB, sharedWith: [{ userId: DANA, accessLevel: 'write', addedAt: T0 }] }
  const sessions = new Map<string, ReaderSession>([
    [ROOM, room],
    [THREAD, thread],
    [OTHER, other],
  ])
  const opens = new Map<string, Set<string>>()
  for (const who of PEOPLE) opens.set(who, new Set(rng.some([...RECORDS, ...NODES], 0.75)))
  opens.set(DANA, new Set(rng.some([...RECORDS, ...NODES], 0.9)))
  const deleted = new Set(rng.some(RECORDS, 0.12))
  const agents = new Map<string, Set<string> | undefined>([
    ['agent-open', new Set(PEOPLE)],
    ['agent-closed', new Set(rng.some(PEOPLE, 0.4))],
    ['agent-unknown', undefined],
  ])
  const chatsRead = new Map<string, Map<string, boolean | undefined>>()
  const projectsRead = new Map<string, Map<string, boolean | undefined>>()
  for (const who of PEOPLE) {
    chatsRead.set(who, new Map([[OTHER, rng.chance(0.5)], [ROOM, rng.chance(0.7) ? true : undefined]]))
    projectsRead.set(
      who,
      new Map<string, boolean | undefined>([
        ['proj-open', true],
        ['proj-closed', rng.chance(0.3)],
        ['proj-unknown', undefined],
      ]),
    )
  }
  const failing = new Set(rng.chance(0.15) ? [rng.pick(PEOPLE)] : [])
  const switchAt = rng.chance(0.1) ? undefined : rng.chance(0.05) ? 'fails' : T1.getTime()

  const rows: Row[] = []
  const named: Named[] = []
  const runs: string[] = []
  const timeline: World['timeline'] = []
  const record = (kind: 'write' | 'change', row: Row): void => {
    timeline.push({ kind, row: structuredClone(row) })
  }
  let seq = 0
  let clock = T0.getTime()
  const tick = (): Date => {
    clock += 60_000
    return new Date(clock)
  }
  const chatOf = (): string => (rng.chance(0.8) ? ROOM : rng.chance(0.6) ? THREAD : OTHER)
  const askerOf = (chat: string): string =>
    chat === THREAD ? (rng.chance(0.8) ? CAROL : DANA) : chat === OTHER ? rng.pick([BOB, DANA]) : rng.pick([DANA, CAROL, BOB, IVY, PRIYA])

  const parentsFor = (): unknown[] => {
    const out: unknown[] = []
    const count = rng.int(4)
    for (let i = 0; i < count && named.length > 0; i += 1) {
      const target = rng.pick(named)
      const ver = rng.chance(0.08) ? `gone-${rng.int(1000)}` : rng.pick(target.vers)
      const kind = target.row.messageType
      const mode =
        kind === 'bot_response' || kind === 'error'
          ? rng.chance(0.55)
            ? 'text'
            : rng.chance(0.8)
              ? 'full'
              : rng.pick(['grant', 'preswitch'])
          : rng.chance(0.85)
            ? 'text'
            : 'full'
      out.push({ id: String(target.row._id), ver, mode })
    }
    if (rng.chance(0.05)) out.push({ id: nextId(T0), ver: nextId(T0), mode: 'text' })
    if (runs.length > 0 && rng.chance(0.15)) {
      const runId = rng.chance(0.85) ? rng.pick(runs) : 'run-nobody'
      // With no run: a file saved before the rules, whose record names its chat and when the version read was written,
      // or one whose record could not be read.
      const before = rng.chance(0.6) && { conversationId: rng.pick([ROOM, OTHER, THREAD]), writtenAt: (rng.chance(0.7) ? T0 : T2).getTime() }
      out.push({ id: '', ver: '', mode: 'full', artifactId: `art-${rng.int(3)}`, ...(rng.chance(0.9) ? { runId } : before) })
    }
    if (rng.chance(0.03)) out.push('not a parent')
    return out
  }

  const labelFor = (asker: string, chat: string, ver: string, content: string, parts: unknown[]): Record<string, unknown> => {
    const direct = emptyDirect()
    direct.records = rng.some(RECORDS, 0.25)
    direct.nodes = rng.some(NODES, 0.12)
    if (rng.chance(0.12)) direct.personal = [rng.chance(0.7) ? asker : rng.pick(PEOPLE)]
    if (rng.chance(0.12)) direct.agents = [{ key: rng.pick(AGENTS), kind: 'config' }]
    if (rng.chance(0.1)) direct.chat = [{ conversationId: rng.pick([chat, OTHER, ROOM, '']), recordId: 'up-1' }]
    if (rng.chance(0.08)) direct.projects = [rng.pick(PROJECTS)]
    if (rng.chance(0.06)) direct.marks = [{ source: 'searx', ver: 1 }]
    const complete = !rng.chance(0.08)
    const label: Record<string, unknown> = {
      v: rng.chance(0.03) ? 6 : 5,
      ver,
      contentHash: labelContentHash(content, parts),
      direct,
      parents: parentsFor(),
      complete,
      tagset: 1,
      pub: rng.chance(0.35),
      ...(!complete && { incomplete: ['untagged_prompt_part:tool'] }),
    }
    if (rng.chance(0.02)) (label.direct as Record<string, unknown>).future = []
    return label
  }

  const turns = 4 + rng.int(18)
  for (let t = 0; t < turns; t += 1) {
    const at = tick()
    const chat = chatOf()
    const asker = askerOf(chat)
    const roll = rng.next()
    if (roll < 0.15) {
      const q: Row = { _id: nextId(at), sessionId: chat, orgId: ORG, seq: ++seq, messageType: rng.chance(0.85) ? 'user_query' : 'note', authorUserId: asker, content: `Q${t}`, createdAt: at }
      rows.push(q)
      record('change', q)
      if (rng.chance(0.15)) {
        q.removedAt = tick()
        record('change', q)
      }
      named.push({ row: q, vers: ['legacy'] })
      continue
    }
    if (roll < 0.25) {
      // An answer from before labels; some were labelled since.
      const old: Row = {
        _id: nextId(at),
        sessionId: chat,
        orgId: ORG,
        seq: ++seq,
        messageType: 'bot_response',
        requestedBy: rng.chance(0.9) ? asker : undefined,
        content: `Old ${t}`,
        referenceData: rng.some(RECORDS, 0.3).map((id) => ({ id, recordId: id })),
        createdAt: rng.chance(0.8) ? T0 : at,
      }
      if (rng.chance(0.15)) {
        old.labelVer = nextId(at)
        old.labelHistory = [{ ver: 'legacy' }]
      }
      rows.push(old)
      record('change', old)
      named.push({ row: old, vers: ['legacy'] })
      continue
    }
    if (roll < 0.32 && named.length > 0) {
      const copy: Row = { _id: nextId(at), sessionId: chat, orgId: ORG, seq: ++seq, messageType: 'shared_answer', authorUserId: asker, requestedBy: asker, content: `Copy ${t}`, createdAt: at }
      rows.push(copy)
      record('change', copy)
      if (rng.chance(0.2)) {
        copy.removedAt = tick()
        record('change', copy)
      }
      named.push({ row: copy, vers: ['legacy'] })
      continue
    }
    if (roll < 0.36) {
      const card: Row = { _id: nextId(at), sessionId: chat, orgId: ORG, seq: ++seq, messageType: 'tool_call', requestedBy: asker, content: 'card', createdAt: at }
      rows.push(card)
      record('change', card)
      continue
    }
    const labelVer = nextId(at)
    const content = `Answer ${t}`
    const parts = [{ type: 'text', content }]
    const runId = `run-${t}`
    runs.push(runId)
    const row: Row = {
      _id: nextId(at),
      sessionId: chat,
      orgId: ORG,
      seq: ++seq,
      messageType: rng.chance(0.9) ? 'bot_response' : 'error',
      requestedBy: rng.chance(0.95) ? asker : undefined,
      content,
      parts,
      labelVer,
      runId,
      createdAt: at,
    }
    if (!rng.chance(0.05)) row.provenance = labelFor(asker, chat, labelVer, content, parts)
    rows.push(row)
    record('write', row)
    named.push({ row, vers: [labelVer] })
    // Shared at once, sometimes: later answers are made knowing it.
    if (rng.chance(0.08)) {
      row.consent = { by: row.requestedBy ?? DANA, at: new Date(clock), ver: labelVer, scope: 'everyone' }
      record('change', row)
    }
  }

  // Later writes: regenerates and removals archive a version; shares of current and archived versions.
  const answers = rows.filter((r) => (r.messageType === 'bot_response' || r.messageType === 'error') && typeof r.labelVer === 'string')
  for (const row of answers) {
    const versions = named.find((n) => n.row === row) as Named
    const rewrites = rng.chance(0.25) ? 1 + rng.int(3) : 0
    for (let i = 0; i < rewrites; i += 1) {
      const at = tick()
      const retire = rng.chance(0.2)
      const history = archivedLabelHistory(row, retire ? at : undefined) ?? []
      row.labelHistory = history
      const labelVer = nextId(at)
      row.labelVer = labelVer
      delete row.consent
      delete row.consentTo
      if (retire) {
        row.removedAt = at
        row.content = ''
        row.parts = []
        delete row.provenance
        versions.vers.push(labelVer)
        record('write', row)
        break
      }
      const content = `Answer again ${i}`
      const parts = [{ type: 'text', content }]
      row.content = content
      row.parts = parts
      const runId = `${String(row.runId)}-again-${i}`
      row.runId = runId
      runs.push(runId)
      row.provenance = labelFor(String(row.requestedBy ?? DANA), String(row.sessionId), labelVer, content, parts)
      versions.vers.push(labelVer)
      record('write', row)
    }
    if (rng.chance(0.08) && typeof row.content === 'string') {
      row.content = `${row.content} (changed behind its label)`
      record('change', row)
    }
  }
  for (const row of answers) {
    if (!rng.chance(0.25)) continue
    const vers = (named.find((n) => n.row === row) as Named).vers
    const ver = rng.pick(vers)
    const scope = rng.chance(0.6) ? 'everyone' : 'people'
    const consent = { by: row.requestedBy ?? DANA, at: T2, ver, scope }
    const people = rng.some([CAROL, BOB, IVY, PRIYA], 0.5)
    const consentTo = people.map((userId) => ({ userId, memberSince: rng.chance(0.85) ? `direct:${T0.toISOString()}` : 'owner' }))
    if (ver === row.labelVer) {
      row.consent = consent
      if (scope === 'people') row.consentTo = consentTo
    } else if (Array.isArray(row.labelHistory)) {
      const entry = (row.labelHistory as Array<Record<string, unknown>>).find((e) => e.ver === ver)
      if (entry) {
        entry.consent = consent
        if (scope === 'people') entry.consentTo = consentTo
      }
    }
    record('change', row)
  }
  for (const n of named) {
    if (n.row.messageType === 'bot_response' && n.row.labelVer === undefined && rng.chance(0.2)) {
      n.row.consent = { by: DANA, at: T2, ver: 'legacy', scope: 'everyone', how: 'earlier' }
      record('change', n.row)
    }
  }
  // Some shares are stopped again.
  for (const row of answers) {
    if (row.consent !== undefined && rng.chance(0.2)) {
      delete row.consent
      delete row.consentTo
      record('change', row)
    }
  }
  const store = new Map(rows.map((r) => [String(r._id), r]))
  return { rows, store, sessions, opens, deleted, agents, chatsRead, projectsRead, failing, switchAt, timeline }
}

/** The shares of a store's rows, as the repository's `listShareMarks` reads them. */
export function shareMarksOf(store: ReadonlyMap<string, Row>, sessionIds: readonly string[]): Array<Record<string, unknown>> {
  const out: Array<Record<string, unknown>> = []
  for (const row of store.values()) {
    if (!sessionIds.includes(String(row.sessionId))) continue
    const history = Array.isArray(row.labelHistory) ? (row.labelHistory as Array<Record<string, unknown>>) : []
    const shared = (row.consent !== null && typeof row.consent === 'object') || history.some((e) => e.consent !== null && typeof e.consent === 'object')
    if (!shared) continue
    out.push({
      _id: String(row._id),
      sessionId: String(row.sessionId),
      ...(typeof row.labelVer === 'string' && { labelVer: row.labelVer }),
      ...(row.consent !== undefined && { consent: row.consent }),
      labelHistory: history.map((e) => ({ ver: e.ver, ...(e.consent !== undefined && { consent: e.consent }) })),
    })
  }
  return out
}

/** Closures kept in memory, as the repository keeps them: by key, each replaced whole; and the shares of the rows. */
export function memoryClosureStore(store: ReadonlyMap<string, Row>) {
  const docs = new Map<string, Record<string, unknown>>()
  const counts = { gets: 0, puts: 0, shares: 0, kept: 0 }
  return {
    docs,
    counts,
    get: async (_org: string, keys: readonly string[]) => {
      counts.gets += 1
      return new Map(keys.flatMap((k) => (docs.has(k) ? [[k, structuredClone(docs.get(k))] as const] : []))) as never
    },
    put: async (_org: string, closures: readonly unknown[]) => {
      counts.puts += 1
      for (const c of closures) {
        counts.kept += 1
        docs.set((c as { _id: string })._id, structuredClone(c as Record<string, unknown>))
      }
    },
    shares: async (_org: string, sessionIds: readonly string[]) => {
      counts.shares += 1
      return shareMarksOf(store, sessionIds) as never
    },
  }
}

/** The reader check's dependencies over a world: the same answers whichever check asks. */
export function depsOf(world: World): Partial<VisibilityDeps> {
  const { store } = world
  return {
    readable: async (who, ids) => {
      if (world.failing.has(who.userId)) {
        return { readable: new Set(), asked: new Set(), deleted: new Set(), failed: true, validUntil: Date.now() + 5_000 }
      }
      const opens = world.opens.get(who.userId) ?? new Set<string>()
      return {
        readable: new Set(ids.filter((id) => opens.has(id) && !world.deleted.has(id))),
        asked: new Set(ids),
        deleted: new Set(ids.filter((id) => world.deleted.has(id))),
        failed: false,
        validUntil: Date.now() + 30_000,
      }
    },
    labels: async (org, ids) =>
      new Map(ids.flatMap((id) => {
        const row = store.get(id)
        return row && org === ORG ? [[id, row] as const] : []
      })),
    runs: async (_org, sessionIds, runIds) =>
      [...store.values()].filter(
        (row) =>
          sessionIds.includes(String(row.sessionId)) &&
          (runIds.includes(row.runId as string) ||
            (Array.isArray(row.labelHistory) &&
              (row.labelHistory as Array<{ runId?: string }>).some((e) => runIds.includes(e.runId as string)))),
      ),
    canUseAgent: async (who, key) => {
      const users = world.agents.get(key)
      return users === undefined ? undefined : users.has(who.userId)
    },
    canReadConversation: async (who, cid) => world.chatsRead.get(who.userId)?.get(cid),
    canReadProject: async (who, pid) => world.projectsRead.get(who.userId)?.get(pid),
    isServiceAgent: async () => false,
    sharedText: (text) => text,
    switchAt: async () => {
      if (world.switchAt === 'fails') throw new Error('down')
      return world.switchAt
    },
    labelRule: async () => true,
    store: new VerdictStore(),
    chats: async (_org, ids) => new Map(ids.flatMap((id) => (world.sessions.has(id) ? [[id, world.sessions.get(id) as ReaderSession] as const] : []))),
    rowsUpTo: async (_org, sessionId, seq, limit) =>
      [...store.values()]
        .filter((r) => r.sessionId === sessionId && typeof r.seq === 'number' && r.seq <= seq)
        .sort((a, b) => (b.seq as number) - (a.seq as number))
        .slice(0, limit),
    perRecordView: async (who, rows) => {
      const opens = world.opens.get(who.userId) ?? new Set<string>()
      return rows.map((m) => {
        const named = ((m as { referenceData?: Array<{ recordId?: string }> }).referenceData ?? []).map((r) => r.recordId)
        if (world.failing.has(who.userId) && named.length > 0) {
          return { ...m, answerHidden: { redacted: true, reason: 'access_check_unavailable' } }
        }
        return named.every((id) => id !== undefined && opens.has(id)) ? m : { ...m, answerHidden: { redacted: true, reason: 'no_longer_accessible' } }
      })
    },
  }
}

/** The readers a world is judged for: each person, a member or not, in each chat they may read. */
export function readersOf(world: World, rng: Rng): Array<{ reader: Reader; session: ReaderSession | undefined; member: boolean | undefined }> {
  const out: Array<{ reader: Reader; session: ReaderSession | undefined; member: boolean | undefined }> = []
  for (const userId of PEOPLE) {
    const sessionId = rng.chance(0.75) ? ROOM : rng.chance(0.6) ? THREAD : rng.chance(0.5) ? OTHER : undefined
    const session = sessionId === undefined ? undefined : world.sessions.get(sessionId)
    const member = userId === ZED ? rng.chance(0.3) : rng.chance(0.85) ? true : rng.chance(0.5) ? undefined : false
    out.push({ reader: { userId, orgId: ORG }, session, member })
  }
  return out
}
