import { expect } from 'chai'
import {
  resetVisibility,
  widenOK,
  type ReaderRow,
  type ReaderSession,
  type VisibilityDeps,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'

/**
 * A share with named people: the asker's answer is checked once for what it was built on, then each person is asked
 * about their own access, a few at a time.
 */

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const DANA = 'd00000000000000000000001'
const T0 = new Date('2026-10-01T10:00:00Z')
const DIRECT_KEYS = ['records', 'vrids', 'nodes', 'saVrids', 'personal', 'agents', 'chat', 'projects', 'marks']

let n = 0
const nextId = (): string => (++n).toString(16).padStart(24, '0')

const person = (i: number): string => `${(0x500000 + i).toString(16)}${'0'.repeat(18)}`

const answer = (records: string[], parents: Array<{ id: string; ver: string; mode: string }> = []): ReaderRow & Record<string, unknown> => {
  const labelVer = nextId()
  const content = `Answer ${labelVer}`
  const parts = [{ type: 'text', content }]
  return {
    _id: nextId(),
    sessionId: ROOM,
    orgId: ORG,
    messageType: 'bot_response',
    requestedBy: DANA,
    content,
    parts,
    labelVer,
    provenance: {
      v: 5,
      ver: labelVer,
      contentHash: labelContentHash(content, parts),
      direct: { ...Object.fromEntries(DIRECT_KEYS.map((k) => [k, []])), records },
      parents,
      complete: true,
      tagset: 2,
      pub: false,
    },
  }
}

const world = (rows: ReaderRow[], canOpen: (userId: string, id: string) => boolean) => {
  const store = new Map(rows.map((r) => [String(r._id), r]))
  const counts = { labels: 0, access: new Map<string, number>(), inFlight: 0, mostAtOnce: 0 }
  const deps: Partial<VisibilityDeps> = {
    readable: async (who, ids) => {
      counts.access.set(who.userId, (counts.access.get(who.userId) ?? 0) + 1)
      counts.inFlight += 1
      counts.mostAtOnce = Math.max(counts.mostAtOnce, counts.inFlight)
      await new Promise((resolve) => setTimeout(resolve, 2))
      counts.inFlight -= 1
      return {
        readable: new Set(ids.filter((id) => canOpen(who.userId, id))),
        asked: new Set(ids),
        deleted: new Set(),
        failed: false,
        validUntil: Date.now() + 30_000,
      }
    },
    labels: async (_org, ids) => {
      counts.labels += 1
      return new Map(ids.flatMap((id) => (store.has(id) ? [[id, store.get(id) as ReaderRow]] : [])))
    },
    runs: async () => [],
    canUseAgent: async () => undefined,
    canReadConversation: async () => undefined,
    canReadProject: async () => undefined,
    isServiceAgent: async () => false,
    sharedText: (text) => text,
    switchAt: async () => T0.getTime(),
    labelRule: async () => true,
    store: new VerdictStore(),
  }
  return { deps, counts }
}

describe('a share with named people', () => {
  afterEach(() => {
    resetVisibility()
  })

  it('with 50 people: one label read, one access request for each person besides the asker\'s own, 8 at a time', async () => {
    const people = Array.from({ length: 50 }, (_, i) => person(i))
    const session: ReaderSession = {
      _id: ROOM,
      orgId: ORG,
      userId: DANA,
      sharedWith: people.map((userId) => ({ userId, accessLevel: 'write', addedAt: T0 })),
    }
    const a = answer(['rec-r'])
    // Every tenth person cannot open R.
    const { deps, counts } = world([a], (userId, id) => userId === DANA || !(id === 'rec-r' && people.indexOf(userId) % 10 === 0))
    const result = await widenOK(a, String(a.labelVer), { userIds: people }, { deps, session })
    expect(result.ok).to.equal(true)
    expect(result.cannotOpen).to.deep.equal(people.filter((_, i) => i % 10 === 0))
    expect(counts.labels).to.equal(1)
    expect(counts.access.get(DANA)).to.equal(1)
    for (const userId of people) expect(counts.access.get(userId), userId).to.equal(1)
    expect(counts.mostAtOnce).to.be.at.most(8)
  })

  it('an answer built on 25 before it: its earlier answers are read once, never once per person', async () => {
    const rows: Array<ReaderRow & Record<string, unknown>> = []
    for (let t = 0; t < 26; t += 1) {
      const last = rows[rows.length - 1]
      rows.push(answer([`rec-${t % 5}`], last ? [{ id: String(last._id), ver: String(last.labelVer), mode: 'full' }] : []))
    }
    const people = Array.from({ length: 20 }, (_, i) => person(i))
    const session: ReaderSession = { _id: ROOM, orgId: ORG, userId: DANA, sharedWith: people.map((userId) => ({ userId, accessLevel: 'write', addedAt: T0 })) }
    const top = rows[rows.length - 1] as ReaderRow & Record<string, unknown>
    const { deps, counts } = world(rows, (userId, id) => userId === DANA || id !== 'rec-3' || people.indexOf(userId) % 2 === 0)
    const result = await widenOK(top, String(top.labelVer), { userIds: people }, { deps, session })
    // The share's own walk reads a level at a time; the people add no read of their own.
    expect(result.ok).to.equal(true)
    expect(counts.labels).to.be.at.most(26)
    expect(result.cannotOpen).to.deep.equal(people.filter((_, i) => i % 2 === 1))
    for (const userId of people) expect(counts.access.get(userId), userId).to.equal(1)
  })

  it("past a version shared with the chat, a file's writer is judged for each person as fullOK judges it", async () => {
    const CAROL = person(1)
    const IVY = person(2)
    const session: ReaderSession = { _id: ROOM, orgId: ORG, userId: DANA, sharedWith: [CAROL, IVY].map((userId) => ({ userId, accessLevel: 'write', addedAt: T0 })) }
    // W wrote a file that B read; S was built on B and is shared with everyone in this chat; A was given S whole.
    const w = { ...answer(['rec-w']), runId: 'run-w' }
    const b = answer(['rec-b'], [{ id: '', ver: '', mode: 'full', artifactId: 'art-1', runId: 'run-w' } as never])
    const sh = answer(['rec-s'], [{ id: String(b._id), ver: String(b.labelVer), mode: 'full' }])
    sh.consent = { by: DANA, at: T0, ver: sh.labelVer, scope: 'everyone' }
    const a = answer(['rec-a'], [{ id: String(sh._id), ver: String(sh.labelVer), mode: 'full' }])
    const rows = [w, b, sh, a]
    const { deps } = world(rows, (userId, id) => userId !== IVY || id !== 'rec-w')
    deps.runs = async (_org, sessions, runIds) => rows.filter((r) => runIds.includes(String(r.runId)) && sessions.includes(String(r.sessionId)))
    // The share check stops at S, so it may be shared, counting on S's share; each person must still open everything A
    // was built on, the file included: Carol can, Ivy cannot open what wrote it.
    const result = await widenOK(a, String(a.labelVer), { userIds: [CAROL, IVY] }, { deps, session })
    expect(result).to.deep.equal({ ok: true, on: [{ row: String(sh._id), ver: String(sh.labelVer) }], cannotOpen: [IVY] })
  })
})
