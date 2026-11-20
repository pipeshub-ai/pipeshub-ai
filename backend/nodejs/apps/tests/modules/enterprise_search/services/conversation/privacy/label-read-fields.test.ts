import { expect } from 'chai'
import {
  includableRows,
  readerView,
  resetVisibility,
  visibleRows,
  widenOK,
  type Reader,
  type ReaderRow,
  type ReaderSession,
  type VisibilityDeps,
  type WidenAudience,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { MESSAGE_LABEL_FIELDS } from '../../../../../../src/libs/repositories/conversation/message-label-fields'

/**
 * The reader check reads stored rows with only `MESSAGE_LABEL_FIELDS`. Every reader must get the same answer from
 * rows read that way as from whole rows: the same verdicts, the same views, the same history and the same shares.
 */

type Row = ReaderRow & Record<string, unknown>

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const DANA = 'd00000000000000000000001'
const CAROL = 'c00000000000000000000002'
const BOB = 'b00000000000000000000003'
const IVY = 'e00000000000000000000004'
const PRIYA = 'f00000000000000000000005'
const ZED = '900000000000000000000006'
const MEMBERS = [DANA, CAROL, BOB, IVY, PRIYA]

const R = 'rec-R'
const R2 = 'rec-R2'
const OPENS: Record<string, string[]> = { [DANA]: [R, R2], [CAROL]: [R, R2], [IVY]: [R], [BOB]: [], [PRIYA]: [], [ZED]: [] }

const T0 = new Date('2026-10-01T10:00:00Z')
const T1 = new Date('2026-10-02T10:00:00Z')
const T2 = new Date('2026-10-03T10:00:00Z')

const session: ReaderSession = {
  _id: ROOM,
  orgId: ORG,
  userId: DANA,
  sharedWith: [
    { userId: CAROL, accessLevel: 'write', addedAt: T0 },
    { userId: BOB, accessLevel: 'write', addedAt: T0 },
    { userId: IVY, accessLevel: 'read', addedAt: T0 },
    { userId: PRIYA, accessLevel: 'write', addedAt: T2 },
  ],
}

let n = 0
const nextId = (): string => (++n).toString(16).padStart(24, '0')

/** What a stored answer carries beside what the check reads. */
const unreadFields = (id: string): Record<string, unknown> => ({
  citations: [{ citationId: `cit-${id}`, relevanceScore: 0.9, excerpt: 'Q3 revenue was 12M.' }],
  referenceData: [{ id: 'PROJ-1', type: 'issue' }],
  attachments: [{ recordId: 'rec-file', recordName: 'plan.pdf' }],
  tools: [{ toolName: 'jira__search_issues', toolResult: { rows: 3 } }],
  reasoning: [{ text: 'read R first' }],
  feedback: [{ userId: BOB, isHelpful: false }],
  followUpQuestions: ['And Q4?'],
  modelInfo: { modelKey: 'k', modelName: 'm' },
  mentions: [{ kind: 'user', id: CAROL }],
  shareRequests: [{ by: BOB, at: T2, ver: id, state: 'open' }],
  seq: 7,
  changeRev: 7,
  updatedAt: T2,
  contentFormat: 'MARKDOWN',
})

interface AnswerOptions {
  requestedBy?: string
  messageType?: string
  direct?: Record<string, unknown>
  parents?: Row[]
  pub?: boolean
  /** No label: written before labels. */
  unlabelled?: boolean
  /** The text the label was made for, when the stored text changed since. */
  labelledText?: string
  consent?: 'everyone' | string[]
  /** Why the label is incomplete. */
  incomplete?: string[]
  sessionId?: string
}

/** An answer stored the way the save seals it, with everything a stored answer carries. */
const answer = (content: string, o: AnswerOptions = {}): Row => {
  const _id = nextId()
  const parts = [
    { type: 'text', content },
    { type: 'tool_call', toolCallId: `call-${_id}`, toolName: 'jira__search_issues', status: 'completed', resultPreview: 'PROJ-1' },
  ]
  const row: Row = {
    _id,
    sessionId: o.sessionId ?? ROOM,
    orgId: ORG,
    messageType: o.messageType ?? 'bot_response',
    ...(o.requestedBy !== '' && { requestedBy: o.requestedBy ?? DANA }),
    content,
    parts,
    createdAt: T1,
    ...unreadFields(_id),
  }
  if (o.unlabelled === true) return row
  const labelVer = nextId()
  const hashed = o.labelledText === undefined ? parts : [{ type: 'text', content: o.labelledText }, parts[1]]
  row.labelVer = labelVer
  row.provenance = {
    v: 5,
    ver: labelVer,
    contentHash: labelContentHash(o.labelledText ?? content, hashed),
    direct: { records: [], vrids: [], nodes: [], saVrids: [], personal: [], agents: [], chat: [], projects: [], marks: [], ...o.direct },
    parents: (o.parents ?? []).map((p) => ({ id: p._id, ver: p.labelVer ?? 'legacy', mode: 'full' })),
    complete: o.incomplete === undefined,
    ...(o.incomplete !== undefined && { incomplete: o.incomplete }),
    tagset: 1,
    pub: o.pub ?? false,
  }
  row.labelHistory = [{ ver: nextId(), runId: 'run-0', provenance: { v: 5, ver: 'older' }, retiredAt: T0 }]
  if (o.consent === 'everyone') row.consent = { by: o.requestedBy ?? DANA, at: T1, ver: labelVer, scope: 'everyone' }
  if (Array.isArray(o.consent)) {
    row.consent = { by: o.requestedBy ?? DANA, at: T1, ver: labelVer, scope: 'people' }
    row.consentTo = o.consent.map((userId) => ({ userId, memberSince: `direct:${T0.toISOString()}` }))
  }
  return row
}

// The chat: Dana's answers that used R, R2, her own mail, nothing, an edited text, an earlier answer (not on the
// page, read by the check) and nothing from before labels; Carol's own answer; a failed run; a copy; a reply.
const pub = answer('Nothing restricted.', { pub: true })
const fromR = answer('Revenue from R.', { direct: { records: [R] } })
const ownMail = answer('From my mail.', { direct: { personal: [DANA] } })
const edited = answer('Edited since.', { labelledText: 'Written first.' })
const builtOnR = answer('Built on the answer from R.', { parents: [fromR] })
const beforeLabels = answer('Before labels.', { unlabelled: true })
const sharedR2 = answer('From R2, shared with everyone.', { direct: { records: [R2] }, consent: 'everyone' })
const sharedWithBob = answer('From R2, shared with Bob.', { direct: { records: [R2] }, consent: [BOB] })
const carols = answer("Carol's answer from R2.", { requestedBy: CAROL, direct: { records: [R2] } })
const failed = answer('The run failed on R.', { messageType: 'error', direct: { records: [R] } })
// Earlier answers the check reads for itself, whose asker only the stored row says: Carol's, with a label that can't
// be relied on, and one from another chat written before labels, with no asker stored.
const carolsUnchecked = answer('Carol, unchecked.', { requestedBy: CAROL, incomplete: ['tool_unclassified'] })
const onCarols = answer("Built on Carol's.", { parents: [carolsUnchecked] })
const elsewhere = answer('From another chat.', { unlabelled: true, requestedBy: '', sessionId: 'cccccccccccccccccccccccc' })
const onElsewhere = answer('Built on the other chat.', { parents: [elsewhere] })
// An answer regenerated since: the version an answer built on lives in its label history, and it named nothing.
const regenerated = answer('The new version.', { direct: { records: [R] } })
const OLD_VER = nextId()
;(regenerated.labelHistory as unknown[]).push({
  ver: OLD_VER,
  runId: 'run-1',
  provenance: { ...(regenerated.provenance as Record<string, unknown>), ver: OLD_VER, direct: { records: [], vrids: [], nodes: [], saVrids: [], personal: [], agents: [], chat: [], projects: [], marks: [] } },
  retiredAt: T1,
})
const onOldVersion = answer('Built on the old version.')
;(onOldVersion.provenance as Record<string, unknown>).parents = [{ id: regenerated._id, ver: OLD_VER, mode: 'full' }]
// Copies of an answer shared to the chat: one still there, one its sharer removed.
const liveCopy: Row = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'shared_answer', authorUserId: CAROL, requestedBy: CAROL, content: 'A copy.', createdAt: T1 }
const removedCopy: Row = { ...liveCopy, _id: nextId(), content: 'A removed copy.', removedAt: T2 }
const onLiveCopy = answer('Built on a copy.', { parents: [liveCopy] })
const onRemovedCopy = answer('Built on a removed copy.', { parents: [removedCopy] })
const copy: Row = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'shared_answer', authorUserId: BOB, requestedBy: BOB, content: 'A copy.', createdAt: T1 }
const question: Row = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'user_query', authorUserId: DANA, content: 'What changed?', createdAt: T1 }
const reply: Row = {
  _id: nextId(),
  sessionId: ROOM,
  orgId: ORG,
  messageType: 'user_query',
  authorUserId: CAROL,
  content: 'About that one',
  createdAt: T1,
  replyTo: { kind: 'answer', messageId: fromR._id, excerpt: 'Revenue from R.' },
}

const STORED = [
  question, pub, fromR, ownMail, edited, builtOnR, beforeLabels, sharedR2, sharedWithBob, carols, failed, carolsUnchecked,
  onCarols, elsewhere, onElsewhere, regenerated, onOldVersion, liveCopy, removedCopy, onLiveCopy, onRemovedCopy, copy, reply,
]
/** Earlier rows the check reads for itself; the page a read shows is the rest. */
const READ_FOR_ITSELF = [fromR, carolsUnchecked, elsewhere, regenerated, liveCopy, removedCopy]
const PAGE = STORED.filter((r) => !READ_FOR_ITSELF.includes(r))

/** A row as a page read gets it: the fields stored apart from the row's text stay in the store. */
const asPaged = (row: Row): Row => {
  const { provenance: _p, labelHistory: _h, consentTo: _c, shareRequests: _s, ...rest } = row
  return rest as Row
}

const onlyReadFields = (row: Row): Row =>
  Object.fromEntries(MESSAGE_LABEL_FIELDS.filter((f) => f in row).map((f) => [f, row[f]])) as Row

/** The reader check over the chat, its label read giving whole rows or only the fields it reads. */
const depsReading = (shape: (row: Row) => Row): Partial<VisibilityDeps> => {
  const store = new Map(STORED.map((r) => [String(r._id), r]))
  return {
    readable: async (who, ids) => {
      const opens = new Set(OPENS[who.userId] ?? [])
      return {
        readable: new Set([...ids].filter((id) => opens.has(id))),
        asked: new Set(ids),
        deleted: new Set(),
        failed: false,
        validUntil: Date.now() + 30_000,
      }
    },
    labels: async (_org, ids) =>
      new Map([...ids].flatMap((id) => (store.has(id) ? [[id, shape(store.get(id) as Row)] as [string, ReaderRow]] : []))),
    canUseAgent: async () => false,
    canReadConversation: async () => undefined,
    canReadProject: async () => undefined,
    isServiceAgent: async () => false,
    sharedText: (text) => text,
    // The org's first labelled answer came after Bob, Carol and Ivy joined and before Priya did.
    switchAt: async () => T1.getTime(),
    labelRule: async () => true,
    store: new VerdictStore(),
  }
}

const reader = (userId: string): Reader => ({ userId, orgId: ORG })
const page = (): Row[] => PAGE.map(asPaged)

/** Verdicts without the time they hold until, which differs between any two reads. */
const settled = (verdicts: Map<string, { verdict: string; reason?: string; basis?: string }>): Record<string, unknown> =>
  Object.fromEntries([...verdicts].map(([id, v]) => [id, { verdict: v.verdict, reason: v.reason, basis: v.basis }]))

describe('rows the reader check reads with only the fields it reads (MESSAGE_LABEL_FIELDS)', () => {
  afterEach(() => resetVisibility())

  const whole = (): Partial<VisibilityDeps> => depsReading((row) => row)
  const trimmed = (): Partial<VisibilityDeps> => depsReading(onlyReadFields)

  it('the fields leave out the sources, tool output, reasoning, files and feedback, and keep the text and steps the label hash covers', () => {
    for (const unread of ['citations', 'referenceData', 'attachments', 'tools', 'reasoning', 'feedback', 'followUpQuestions', 'modelInfo', 'mentions', 'shareRequests']) {
      expect(MESSAGE_LABEL_FIELDS as readonly string[], unread).to.not.include(unread)
    }
    expect(MESSAGE_LABEL_FIELDS).to.include.members(['content', 'parts', 'labelVer', 'provenance', 'labelHistory', 'requestedBy', 'authorUserId', 'sessionId', 'messageType', 'consent', 'consentTo', 'removedAt'])
    const read = onlyReadFields(fromR)
    expect(read).to.not.have.any.keys('citations', 'referenceData', 'attachments', 'tools', 'reasoning', 'feedback', 'shareRequests')
  })

  it('every reader, member or not, gets the same verdict on every row', async () => {
    for (const who of [...MEMBERS, ZED]) {
      const opts = { member: who !== ZED, fresh: true }
      const a = await visibleRows(reader(who), session, page(), { ...opts, deps: whole() })
      const b = await visibleRows(reader(who), session, page(), { ...opts, deps: trimmed() })
      expect(settled(b), who).to.deep.equal(settled(a))
    }
  })

  it('the verdicts are the rule, not a blanket: R and what built on it hide from Bob, mail from Carol, an edited text from all but its asker', async () => {
    const of = async (who: string) => settled(await visibleRows(reader(who), session, page(), { member: true, fresh: true, deps: trimmed() }))
    const carol = await of(CAROL)
    const bob = await of(BOB)
    const priya = await of(PRIYA)
    const dana = await of(DANA)
    expect((carol[String(builtOnR._id)] as { verdict: string }).verdict).to.equal('visible')
    expect((bob[String(builtOnR._id)] as { verdict: string }).verdict).to.equal('hidden')
    expect((carol[String(ownMail._id)] as { verdict: string }).verdict).to.equal('hidden')
    expect((carol[String(edited._id)] as { verdict: string }).verdict).to.equal('hidden')
    expect((dana[String(edited._id)] as { verdict: string }).verdict).to.equal('visible')
    expect((bob[String(sharedR2._id)] as { verdict: string }).verdict).to.equal('consented')
    expect((bob[String(sharedWithBob._id)] as { verdict: string }).verdict).to.equal('consented')
    expect((priya[String(sharedWithBob._id)] as { verdict: string }).verdict).to.equal('hidden')
    expect((bob[String(beforeLabels._id)] as { verdict: string }).verdict).to.equal('visible')
    expect((priya[String(beforeLabels._id)] as { verdict: string }).verdict).to.equal('hidden')
    expect((dana[String(carols._id)] as { verdict: string }).verdict).to.equal('visible')
    expect((bob[String(carols._id)] as { verdict: string }).verdict).to.equal('hidden')
    expect((bob[String(failed._id)] as { verdict: string }).verdict).to.equal('hidden')
    // Built on an answer only its stored asker may see: hidden from Dana, though she asked it; Carol asked the earlier one.
    expect((dana[String(onCarols._id)] as { verdict: string }).verdict).to.equal('hidden')
    expect((carol[String(onCarols._id)] as { verdict: string }).verdict).to.equal('visible')
    expect((dana[String(onElsewhere._id)] as { verdict: string }).verdict).to.equal('hidden')
    // An archived version is judged by its archived label; a removed copy no longer counts as shared.
    expect((bob[String(onOldVersion._id)] as { verdict: string }).verdict).to.equal('visible')
    expect((bob[String(onLiveCopy._id)] as { verdict: string }).verdict).to.equal('visible')
    expect((bob[String(onRemovedCopy._id)] as { verdict: string }).verdict).to.equal('hidden')
  })

  it('every reader is given the same rows, a reply keeping the words it quotes only where the answer it quotes is shown', async () => {
    const perRecordView = async <R extends object>(list: R[]): Promise<R[]> => list
    for (const who of [...MEMBERS, ZED]) {
      const opts = { member: who !== ZED, fresh: true, perRecordView }
      const a = await readerView(reader(who), session, page(), { ...opts, deps: whole() })
      const b = await readerView(reader(who), session, page(), { ...opts, deps: trimmed() })
      expect(b.rows, who).to.deep.equal(a.rows)
    }
    const bobs = await readerView(reader(BOB), session, page(), { member: true, fresh: true, perRecordView, deps: trimmed() })
    const quoted = bobs.rows.find((r) => r._id === reply._id) as Row
    expect((quoted.replyTo as Record<string, unknown>).excerpt).to.equal('')
  })

  it('every sender is given the same history, with the same row, version and public marks', async () => {
    for (const who of MEMBERS) {
      const a = await includableRows(reader(who), session, page(), { fresh: true, deps: whole() })
      const b = await includableRows(reader(who), session, page(), { fresh: true, deps: trimmed() })
      expect(b, who).to.deep.equal(a)
    }
  })

  it('every share of the asker gets the same answer', async () => {
    const audiences: WidenAudience[] = ['everyone', 'outside', { userIds: [CAROL, BOB] }]
    for (const row of [pub, fromR, ownMail, edited, builtOnR, sharedR2, carols]) {
      for (const audience of audiences) {
        const a = await widenOK(asPaged(row), String(row.labelVer), audience, { session, deps: whole() })
        const b = await widenOK(asPaged(row), String(row.labelVer), audience, { session, deps: trimmed() })
        expect(b, `${String(row.content)} to ${JSON.stringify(audience)}`).to.deep.equal(a)
      }
    }
  })
})
