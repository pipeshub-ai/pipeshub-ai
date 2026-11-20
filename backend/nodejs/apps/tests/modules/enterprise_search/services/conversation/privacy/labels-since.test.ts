import { expect } from 'chai'
import sinon from 'sinon'
import {
  resetVisibility,
  visibilityDeps,
  visibleRows,
  type ReaderRow,
  type VisibilityDeps,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { setConversationRepository } from '../../../../../../src/libs/repositories/conversation'
import type { ConversationRepository } from '../../../../../../src/libs/repositories/conversation/types'

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const OTHER_ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb'
// A label version is an ObjectId: its first eight hex digits are the second it was minted.
const FIRST_VER = '6730d2400000000000000000'
const FIRST_AT = parseInt(FIRST_VER.slice(0, 8), 16) * 1000

/** A repository whose `firstLabelVersion` answers only when let go, counting the calls. */
const heldStore = (answer: () => Promise<string | undefined>) => {
  const calls: string[] = []
  let release: () => void = () => undefined
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  setConversationRepository({
    firstLabelVersion: async (orgId: string) => {
      calls.push(orgId)
      await held
      return answer()
    },
  } as unknown as ConversationRepository)
  return { calls, release: () => release() }
}

describe('when an org started labelling its answers, read by many requests at once', () => {
  beforeEach(() => resetVisibility())
  afterEach(() => {
    sinon.restore()
    setConversationRepository(undefined)
    resetVisibility()
  })

  it('asks the store once for reads of the same org that arrive together', async () => {
    const store = heldStore(async () => FIRST_VER)
    const reads = Array.from({ length: 20 }, () => visibilityDeps().switchAt(ORG))
    store.release()
    const times = await Promise.all(reads)
    expect(store.calls).to.deep.equal([ORG])
    expect(times).to.deep.equal(Array.from({ length: 20 }, () => FIRST_AT))
  })

  it('asks once per org, each for its own org', async () => {
    const store = heldStore(async () => undefined)
    const reads = [ORG, OTHER_ORG, ORG, OTHER_ORG].map((org) => visibilityDeps().switchAt(org))
    store.release()
    expect(await Promise.all(reads)).to.deep.equal([undefined, undefined, undefined, undefined])
    expect([...store.calls].sort()).to.deep.equal([ORG, OTHER_ORG])
  })

  it('keeps the time once known, so later reads ask nothing', async () => {
    const store = heldStore(async () => FIRST_VER)
    store.release()
    expect(await visibilityDeps().switchAt(ORG)).to.equal(FIRST_AT)
    expect(await visibilityDeps().switchAt(ORG)).to.equal(FIRST_AT)
    expect(store.calls).to.have.length(1)
  })

  it('keeps "no labelled answer yet" no longer than a verdict lives (30 s)', async () => {
    const clock = sinon.useFakeTimers({ now: Date.UTC(2026, 10, 20), toFake: ['Date'] })
    const store = heldStore(async () => undefined)
    store.release()
    expect(await visibilityDeps().switchAt(ORG)).to.equal(undefined)
    clock.tick(29_000)
    expect(await visibilityDeps().switchAt(ORG)).to.equal(undefined)
    expect(store.calls).to.have.length(1)
    clock.tick(1_001)
    expect(await visibilityDeps().switchAt(ORG)).to.equal(undefined)
    expect(store.calls).to.have.length(2)
  })

  it('asks again at once when a read holds a labelled answer, however recent "none yet" is', async () => {
    const stored: { first?: string } = {}
    const store = heldStore(async () => stored.first)
    store.release()
    expect(await visibilityDeps().switchAt(ORG)).to.equal(undefined)
    stored.first = FIRST_VER
    expect(await visibilityDeps().switchAt(ORG, true)).to.equal(FIRST_AT)
    expect(store.calls).to.have.length(2)
    // Known now: no read asks again, labelled or not.
    expect(await visibilityDeps().switchAt(ORG, true)).to.equal(FIRST_AT)
    expect(await visibilityDeps().switchAt(ORG)).to.equal(FIRST_AT)
    expect(store.calls).to.have.length(2)
  })

  it('a read that holds a labelled answer does not wait on an older "none yet" question still in flight', async () => {
    const stored: { first?: string } = {}
    let release: () => void = () => undefined
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const calls: string[] = []
    setConversationRepository({
      firstLabelVersion: async (orgId: string) => {
        calls.push(orgId)
        // Only the first question waits: it was asked before the labelled answer was written.
        if (calls.length === 1) {
          await held
          return undefined
        }
        return stored.first
      },
    } as unknown as ConversationRepository)
    const older = visibilityDeps().switchAt(ORG)
    stored.first = FIRST_VER
    expect(await visibilityDeps().switchAt(ORG, true)).to.equal(FIRST_AT)
    release()
    expect(await older).to.equal(undefined)
    // The older question's "none yet" does not replace the time the newer one found.
    expect(await visibilityDeps().switchAt(ORG)).to.equal(FIRST_AT)
    expect(calls).to.have.length(2)
  })

  it('fails every waiting read when the store fails, and the next read asks again', async () => {
    let failing = true
    const store = heldStore(async () => {
      if (failing) throw new Error('store down')
      return FIRST_VER
    })
    const reads = [visibilityDeps().switchAt(ORG), visibilityDeps().switchAt(ORG)]
    store.release()
    const settled = await Promise.allSettled(reads)
    expect(settled.map((s) => s.status)).to.deep.equal(['rejected', 'rejected'])
    expect(store.calls).to.have.length(1)
    failing = false
    expect(await visibilityDeps().switchAt(ORG)).to.equal(FIRST_AT)
    expect(store.calls).to.have.length(2)
  })
})

describe('the reader check says when the rows it judges hold a labelled answer', () => {
  const ROOM = 'cccccccccccccccccccccccc'
  const DANA = 'd00000000000000000000001'
  const CAROL = 'c00000000000000000000002'
  let seq = 0
  const row = (labelled: boolean): ReaderRow => {
    seq += 1
    const id = `e${String(seq).padStart(23, '0')}`
    const content = `Answer ${seq}.`
    const base = { _id: id, sessionId: ROOM, orgId: ORG, seq, messageType: 'bot_response', content, requestedBy: DANA }
    if (!labelled) return base as unknown as ReaderRow
    const ver = `f${String(seq).padStart(23, '0')}`
    const direct = { records: [], vrids: [], nodes: [], saVrids: [], personal: [], agents: [], chat: [], projects: [], marks: [] }
    const provenance = { v: 5, ver, contentHash: labelContentHash(content, undefined), direct, parents: [], complete: true, tagset: 1, pub: true }
    return { ...base, labelVer: ver, provenance } as unknown as ReaderRow
  }
  const judgeWith = async (rows: ReaderRow[]) => {
    const seen: Array<boolean | undefined> = []
    const store = new Map(rows.map((r) => [String(r._id), r]))
    const deps: Partial<VisibilityDeps> = {
      readable: async (_who, ids) => ({ readable: new Set(ids), asked: new Set(ids), deleted: new Set(), failed: false, validUntil: Date.now() + 30_000 }),
      labels: async (_org, ids) => new Map(ids.flatMap((id) => (store.has(id) ? [[id, store.get(id) as ReaderRow]] : []))),
      runs: async () => [],
      switchAt: async (_org, labelledSeen) => {
        seen.push(labelledSeen)
        return undefined
      },
      labelRule: async () => true,
      store: new VerdictStore(),
    }
    const session = { _id: ROOM, orgId: ORG, userId: DANA, sharedWith: [{ userId: CAROL, accessLevel: 'write' }] }
    await visibleRows({ userId: CAROL, orgId: ORG }, session as never, rows, { deps, member: true })
    return seen
  }

  afterEach(() => resetVisibility())

  it('a page that holds a labelled answer beside answers from before labels asks with that said', async () => {
    expect(await judgeWith([row(false), row(true)])).to.deep.equal([true])
  })

  it('a page of answers from before labels alone asks without it', async () => {
    expect(await judgeWith([row(false), row(false)])).to.deep.equal([false])
  })
})
