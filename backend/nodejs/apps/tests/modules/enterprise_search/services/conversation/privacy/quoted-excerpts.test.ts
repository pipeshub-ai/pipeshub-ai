import { expect } from 'chai'
import {
  configureVisibility,
  readerView,
  resetVisibility,
  withoutQuotedExcerpt,
  type Reader,
  type ReaderRow,
  type ReaderSession,
  type VisibilityDeps,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'

/** What a reader is given of a quote's words: a question's own words only, and a shared answer's as text only. */

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const DANA = 'd00000000000000000000001'
const CAROL = 'c00000000000000000000002'
const BOB = 'b00000000000000000000003'
const R = 'rec-R'
const OPENS: Record<string, string[]> = { [DANA]: [R], [CAROL]: [R], [BOB]: [] }
const T0 = new Date('2026-10-01T10:00:00Z')
const T1 = new Date('2026-10-02T10:00:00Z')
const HEADER = 'Quoted excerpt:\n"""\n'
const FOOTER = '\n"""\n\n'

const session: ReaderSession = {
  _id: ROOM,
  orgId: ORG,
  userId: DANA,
  sharedWith: [
    { userId: CAROL, accessLevel: 'write', addedAt: T0 },
    { userId: BOB, accessLevel: 'write', addedAt: T0 },
  ],
}
const reader = (userId: string): Reader => ({ userId, orgId: ORG })

let n = 0
const nextId = (): string => (++n).toString(16).padStart(24, '0')

const answer = (content: string, over: Record<string, unknown> = {}): ReaderRow & Record<string, unknown> => {
  const labelVer = nextId()
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
    createdAt: T1,
    provenance: {
      v: 5,
      ver: labelVer,
      contentHash: labelContentHash(content, parts),
      direct: { records: [R], vrids: [], nodes: [], saVrids: [], personal: [], agents: [], chat: [], projects: [], marks: [] },
      parents: [],
      complete: true,
      tagset: 1,
      pub: false,
    },
    ...over,
  }
}

const question = (content: string, authorUserId = DANA, over: Record<string, unknown> = {}): ReaderRow & Record<string, unknown> => ({
  _id: nextId(),
  sessionId: ROOM,
  messageType: 'user_query',
  authorUserId,
  content,
  createdAt: T1,
  ...over,
})

const depsOver = (rows: ReaderRow[]): Partial<VisibilityDeps> => {
  const store = new Map<string, ReaderRow>(rows.map((r) => [String(r._id), r]))
  return {
    readable: async (who, ids) => {
      const opens = new Set(OPENS[who.userId] ?? [])
      return { readable: new Set(ids.filter((id) => opens.has(id))), asked: new Set(ids), deleted: new Set(), failed: false, validUntil: Date.now() + 30_000 }
    },
    labels: async (_org, ids) => new Map(ids.flatMap((id) => (store.has(id) ? [[id, store.get(id) as ReaderRow]] : []))),
    runs: async () => [],
    canUseAgent: async () => false,
    canReadConversation: async () => undefined,
    canReadProject: async () => undefined,
    isServiceAgent: async () => false,
    switchAt: async () => T0.getTime(),
    labelRule: async () => true,
    store: new VerdictStore(),
  }
}

const view = async (who: string, rows: ReaderRow[], all: ReaderRow[] = rows) =>
  (
    await readerView(reader(who), session, rows, {
      deps: depsOver(all),
      member: true,
      perRecordView: async <M extends object>(list: M[]): Promise<M[]> => list,
    })
  ).rows as Array<Record<string, unknown>>

describe('quoted words a reader is given', () => {
  afterEach(() => resetVisibility())

  describe('withoutQuotedExcerpt', () => {
    it('an excerpt holding the closing mark gives others none of its words, only the question after the last mark', () => {
      const excerpt = 'def plan():\n"""\nThe board deck\n"""\n\nreturn "R says revenue grew"'
      expect(withoutQuotedExcerpt(`${HEADER}${excerpt}${FOOTER}Is this right?`)).to.equal('Is this right?')
    })

    it('a quote that never closes, or one cut short by a trim, leaves nothing', () => {
      expect(withoutQuotedExcerpt(`${HEADER}R says revenue grew`)).to.equal('')
      expect(withoutQuotedExcerpt(`${HEADER}R says revenue grew${FOOTER}`.trimEnd())).to.equal('')
    })
  })

  it("Bob gets Dana's quoting question without any of the excerpt's words; Dana keeps it whole", async () => {
    const a = answer('return "R says revenue grew"')
    const excerpt = 'def plan():\n"""\nThe board deck\n"""\n\nreturn "R says revenue grew"'
    const q = question(`${HEADER}${excerpt}${FOOTER}Is this right?`)
    const bob = await view(BOB, [q], [a, q])
    expect(bob[0]?.content).to.equal('Is this right?')
    const dana = await view(DANA, [q], [a, q])
    expect(String(dana[0]?.content)).to.include('R says revenue grew')
  })

  it('a reply excerpt of a question that still opens with a quote is withheld from every reader, its author included', async () => {
    const quoting = question(`${HEADER}R says revenue grew${FOOTER}Why?`)
    const stored = 'Quoted excerpt: """ R says revenue grew """ Why?'
    const reply = question('Agreed', BOB, { replyTo: { messageId: quoting._id, kind: 'message', excerpt: stored, authorUserId: DANA } })
    for (const who of [BOB, CAROL, DANA]) {
      const [row] = await view(who, [reply], [quoting, reply])
      expect((row?.replyTo as { excerpt: string; withheld?: boolean }).excerpt, who).to.equal('')
      expect((row?.replyTo as { withheld?: boolean }).withheld, who).to.equal(true)
    }
  })

  it("a question's own words stay in a reply excerpt", async () => {
    const plain = question('Why did revenue grow?')
    const reply = question('Agreed', BOB, { replyTo: { messageId: plain._id, kind: 'message', excerpt: 'Why did revenue grow?', authorUserId: DANA } })
    const [row] = await view(CAROL, [reply], [plain, reply])
    expect((row?.replyTo as { excerpt: string }).excerpt).to.equal('Why did revenue grow?')
  })

  it("an answer shared with the reader gives them its excerpt as the share's text; a reader who opens R gets it whole", async () => {
    configureVisibility({ sharedText: (text) => text.replace(/<img[^>]*>/g, '').replace(/\s+/g, ' ').trim() })
    const image = '<img src="held-image:https%3A%2F%2Fbeacon.example%2Fp.png%3Fd%3DR-URL">'
    const a = answer(`R says revenue grew ${image}`)
    a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
    const reply = question('And this?', CAROL, { replyTo: { messageId: a._id, kind: 'answer', excerpt: `R says revenue grew ${image}`, authorUserId: DANA } })
    const [bob] = await view(BOB, [reply], [a, reply])
    expect((bob?.replyTo as { excerpt: string }).excerpt).to.equal('R says revenue grew')
    const [carol] = await view(CAROL, [reply], [a, reply])
    expect((carol?.replyTo as { excerpt: string }).excerpt).to.include('held-image')
  })

  describe('a reply excerpt is a copy of one version of the answer it quotes', () => {
    /** Dana's answer as it is after a regenerate: a new version Bob may see, the old one kept in its history. */
    const regenerated = (): ReaderRow & Record<string, unknown> => {
      const a = answer('2 + 2 = 4.')
      ;(a.provenance as { direct: { records: string[] } }).direct.records = []
      ;(a.provenance as { pub: boolean }).pub = true
      a.labelHistory = [{ ver: 'old-version', runId: 'run-1', retiredAt: T1, provenance: { v: 5, direct: { records: [R] } } }]
      return a
    }

    it('a copy of an older version is withheld from every reader, though the answer now is one they may see', async () => {
      const a = regenerated()
      const bobs = question('Noted, thanks.', BOB, { replyTo: { messageId: a._id, kind: 'answer', excerpt: 'R says the codeword', authorUserId: DANA, ver: 'old-version' } })
      const carols = question('Thanks', CAROL, { replyTo: { messageId: a._id, kind: 'answer', excerpt: 'R says the codeword', authorUserId: DANA, ver: 'old-version' } })
      for (const who of [BOB, CAROL, DANA]) {
        const rows = await view(who, [bobs, carols], [a, bobs, carols])
        for (const row of rows) {
          expect((row.replyTo as { excerpt: string }).excerpt, who).to.equal('')
          expect(row.replyTo, who).to.not.have.property('ver')
        }
      }
    })

    it('a copy of the version the reader may see now is given, without the version it records', async () => {
      const a = regenerated()
      const reply = question('Nice', CAROL, { replyTo: { messageId: a._id, kind: 'answer', excerpt: '2 + 2 = 4.', authorUserId: DANA, ver: a.labelVer } })
      const [row] = await view(BOB, [reply], [a, reply])
      expect(row?.replyTo).to.deep.include({ excerpt: '2 + 2 = 4.' })
      expect(row?.replyTo).to.not.have.property('ver')
    })

    it('a share of a later version never gives back the copy of an earlier one', async () => {
      configureVisibility({ sharedText: (text) => text })
      const a = answer('R says the codeword, again')
      a.labelHistory = [{ ver: 'old-version', runId: 'run-1', retiredAt: T1 }]
      a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
      const reply = question('Noted', CAROL, { replyTo: { messageId: a._id, kind: 'answer', excerpt: 'R says the first codeword', authorUserId: DANA, ver: 'old-version' } })
      const [row] = await view(BOB, [reply], [a, reply])
      expect(row?.replyTo).to.deep.include({ excerpt: '', withheld: true })
    })

    it('a copy stored with no version is withheld once the answer has had another version, and judged as before when not', async () => {
      const a = regenerated()
      const old = question('Thanks', CAROL, { replyTo: { messageId: a._id, kind: 'answer', excerpt: 'R says the codeword', authorUserId: DANA } })
      for (const who of [BOB, CAROL]) {
        const [row] = await view(who, [old], [a, old])
        expect((row?.replyTo as { excerpt: string }).excerpt, who).to.equal('')
      }
      const once = answer('2 + 2 = 4.')
      ;(once.provenance as { direct: { records: string[] } }).direct.records = []
      const plain = question('Thanks', CAROL, { replyTo: { messageId: once._id, kind: 'answer', excerpt: '2 + 2 = 4.', authorUserId: DANA } })
      const [row] = await view(BOB, [plain], [once, plain])
      expect((row?.replyTo as { excerpt: string }).excerpt).to.equal('2 + 2 = 4.')
    })
  })

  it('with no share text to give, a shared answer\'s excerpt is withheld', async () => {
    const a = answer('R says revenue grew')
    a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
    const reply = question('And this?', CAROL, { replyTo: { messageId: a._id, kind: 'answer', excerpt: 'R says revenue grew', authorUserId: DANA } })
    const [bob] = await view(BOB, [reply], [a, reply])
    expect((bob?.replyTo as { excerpt: string; withheld?: boolean })).to.deep.include({ excerpt: '', withheld: true })
  })
})
