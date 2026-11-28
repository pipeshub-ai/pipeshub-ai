import { expect } from 'chai'
import sinon from 'sinon'
import {
  newReadable,
  redactMessagesFor,
  redactWithTurns,
  storedAnswerShown,
  viewerOf,
  withWholeTurns,
  type Readable,
  type ViewerMessage,
} from '../../../../src/modules/enterprise_search/utils/viewer-redaction'
import { settlesAt } from '../../../helpers/settles-at'

/**
 * The per-record view withholds an answer whole when anything its turn's question names is unreadable to the reader.
 * These pin that the question is always part of the judgement: when a page holds the answer without
 * it, when a view already replaced the question's sources for the reader, and when a frame begins mid-turn.
 */

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const DANA = 'd00000000000000000000001'
const CAROL = 'c00000000000000000000002'
const BOB = 'b00000000000000000000003'
const BOARD = { id: 'kb-board', name: 'Bluebird board', nodeType: 'kb', connector: 'KB' }
const LOST = { redacted: true, reason: 'no_longer_accessible' }
const UNCONFIRMED = { redacted: true, reason: 'access_check_unavailable' }
const appConfig: any = undefined

/** What the access service would say, settled up front so no request is made: Carol opens the board, Bob nothing. */
const settled = (opens: string[]): Readable => {
  const known = newReadable()
  known.asked.add(`i:${BOARD.id}`)
  for (const id of opens) known.ids.add(id)
  return known
}
const knownFor = (who: string): Readable => settled(who === BOB ? [] : [BOARD.id])
const ownerOf = (m: ViewerMessage): string | undefined =>
  (m as { authorUserId?: string; requestedBy?: string }).authorUserId ?? (m as { requestedBy?: string }).requestedBy ?? DANA

const question = (seq: number, over: Record<string, unknown> = {}): ViewerMessage & Record<string, unknown> => ({
  _id: `q${seq}`,
  sessionId: ROOM,
  seq,
  messageType: 'user_query',
  content: 'What is the codeword?',
  authorUserId: DANA,
  appliedFilters: { apps: [], kb: [BOARD] },
  ...over,
})
const answer = (seq: number, over: Record<string, unknown> = {}): ViewerMessage & Record<string, unknown> => ({
  _id: `a${seq}`,
  sessionId: ROOM,
  seq,
  messageType: 'bot_response',
  content: 'The codeword is pcc-canary.',
  requestedBy: DANA,
  citations: [],
  ...over,
})
const plain = (seq: number): ViewerMessage & Record<string, unknown> =>
  question(seq, { content: 'What is 2 + 2?', appliedFilters: { apps: [], kb: [] } })
const note = (seq: number): ViewerMessage & Record<string, unknown> =>
  ({ _id: `n${seq}`, sessionId: ROOM, seq, messageType: 'note', content: 'See you at three.', authorUserId: CAROL })
const line = (seq: number): ViewerMessage & Record<string, unknown> =>
  ({ _id: `s${seq}`, sessionId: ROOM, seq, messageType: 'system', content: '', authorUserId: DANA })

/** A store holding `rows`: rows before a place, newest first, as the repository reads them. */
const store = (rows: Array<ViewerMessage & Record<string, unknown>>) =>
  sinon.stub().callsFake(async (_sid: string, beforeSeq: number, limit: number) =>
    rows.filter((r) => (r.seq as number) < beforeSeq).sort((a, b) => (b.seq as number) - (a.seq as number)).slice(0, limit),
  )
/** A store that never answers, as a database that is not connected holds a read. */
const silent = () => sinon.stub().returns(new Promise(() => undefined))

const hiddenOf = (m: object | undefined) => (m as { answerHidden?: unknown } | undefined)?.answerHidden

describe('the per-record view judges an answer with its whole turn', () => {
  afterEach(() => sinon.restore())

  describe('redactMessagesFor', () => {
    it('hides the answer from a reader who cannot open what its question named (control)', async () => {
      const out = await redactMessagesFor(appConfig, viewerOf(BOB, ORG), [question(1), answer(2)], ownerOf, { known: knownFor(BOB) })
      expect(hiddenOf(out[1])).to.deep.equal(LOST)
    })

    it('still hides it when a view already replaced the question’s source with a marker for that reader', async () => {
      // The detail names a question's sources for the reader before the per-record view runs.
      const shaped = question(1, { appliedFilters: { apps: [], kb: [{ redacted: true, reason: 'no_access', nodeType: 'kb' }] } })
      const out = await redactMessagesFor(appConfig, viewerOf(BOB, ORG), [shaped, answer(2)], ownerOf, { known: knownFor(BOB) })
      expect(hiddenOf(out[1])).to.deep.equal(LOST)
    })

    it('a reader who can open the source reads the answer (control)', async () => {
      const out = await redactMessagesFor(appConfig, viewerOf(CAROL, ORG), [question(1), answer(2)], ownerOf, { known: knownFor(CAROL) })
      expect(hiddenOf(out[1])).to.equal(undefined)
      expect((out[1] as ViewerMessage).content).to.equal('The codeword is pcc-canary.')
    })
  })

  describe('redactWithTurns, rows that carry their place (a feed page)', () => {
    it('reads back the question of an answer the page holds alone, and hides it from Bob', async () => {
      const rowsBefore = store([question(1), answer(2)])
      const [bob] = await redactWithTurns(appConfig, viewerOf(BOB, ORG), [answer(2)], ownerOf, { known: knownFor(BOB), rowsBefore })
      const [carol] = await redactWithTurns(appConfig, viewerOf(CAROL, ORG), [answer(2)], ownerOf, { known: knownFor(CAROL), rowsBefore })
      expect(hiddenOf(bob)).to.deep.equal(LOST)
      expect(hiddenOf(carol)).to.equal(undefined)
      expect(rowsBefore.called).to.equal(true)
    })

    it('a page that fills every place down to the question asks the store nothing', async () => {
      const rowsBefore = store([])
      const out = await redactWithTurns(appConfig, viewerOf(BOB, ORG), [question(1), answer(2)], ownerOf, { known: knownFor(BOB), rowsBefore })
      expect(hiddenOf(out[1])).to.deep.equal(LOST)
      expect(rowsBefore.called).to.equal(false)
    })

    it('a place the page leaves out is read: a card reply there counts with the question', async () => {
      // Seq 3 (the reply to a card, naming the board) is not on the page of changed rows.
      const reply = question(3, { _id: 'r3', content: 'User selections: the board', appliedFilters: { apps: [], kb: [BOARD] } })
      const rowsBefore = store([plain(1), answer(2), reply, answer(4)])
      const out = await redactWithTurns(appConfig, viewerOf(BOB, ORG), [plain(1), answer(4)], ownerOf, { known: knownFor(BOB), rowsBefore })
      expect(hiddenOf(out[1])).to.deep.equal(LOST)
    })

    it('an answer is judged with its own question, never an earlier one the page happens to hold', async () => {
      // The page holds turn 1 whole and the answer at 5, whose question (4) named the board and is not on the page.
      const rowsBefore = store([plain(1), answer(2), question(4), answer(5)])
      const page = [plain(1), answer(2), answer(5)]
      const out = await redactWithTurns(appConfig, viewerOf(BOB, ORG), page, ownerOf, { known: knownFor(BOB), rowsBefore })
      expect(hiddenOf(out[1])).to.equal(undefined)
      expect(hiddenOf(out[2])).to.deep.equal(LOST)
    })

    it('a turn the store cannot read leaves its answer hidden as unconfirmed', async () => {
      const rowsBefore = sinon.stub().rejects(new Error('store down'))
      const [out] = await redactWithTurns(appConfig, viewerOf(CAROL, ORG), [answer(2)], ownerOf, { known: knownFor(CAROL), rowsBefore })
      expect(hiddenOf(out)).to.deep.equal(UNCONFIRMED)
    })

    it('a turn whose question lies past the read limit is unconfirmed', async () => {
      const long = [question(0), ...Array.from({ length: 600 }, (_, i) => answer(i + 1, { _id: `x${i + 1}` }))]
      const [out] = await redactWithTurns(appConfig, viewerOf(CAROL, ORG), [answer(601)], ownerOf, { known: knownFor(CAROL), rowsBefore: store(long) })
      expect(hiddenOf(out)).to.deep.equal(UNCONFIRMED)
    })

    it('a turn the store has not answered by the read’s deadline leaves its answer unconfirmed, at the deadline', async () => {
      const known = knownFor(CAROL)
      const [out] = await settlesAt(50, () => {
        known.deadline = Date.now() + 50
        return redactWithTurns(appConfig, viewerOf(CAROL, ORG), [answer(2)], ownerOf, { known, rowsBefore: silent() })
      })
      expect(hiddenOf(out)).to.deep.equal(UNCONFIRMED)
    })
  })

  describe('redactWithTurns, a frame (one run of the chat, rows with no place)', () => {
    const bare = (m: Record<string, unknown>) => {
      const rest = { ...m }
      delete rest.seq
      delete rest.sessionId
      return rest as ViewerMessage
    }

    it('a run that begins mid-turn reads its first turn back, and the answer stays hidden from Bob', async () => {
      const rowsBefore = store([question(1), answer(2)])
      const placesOf = sinon.stub().resolves(new Map([['a2', { sessionId: ROOM, seq: 2 }]]))
      const run = [bare(answer(2))]
      const [out] = await redactWithTurns(appConfig, viewerOf(BOB, ORG), run, ownerOf, { known: knownFor(BOB), sessionId: ROOM, rowsBefore, placesOf })
      expect(hiddenOf(out)).to.deep.equal(LOST)
      expect(placesOf.firstCall.args[0]).to.deep.equal(['a2'])
    })

    it('the answers the reader check passes on are judged off the whole run, each with its own question', async () => {
      const run = [bare(plain(1)), bare(answer(2)), bare(question(3)), bare(answer(4))]
      const rowsBefore = store([])
      const out = await redactWithTurns(appConfig, viewerOf(BOB, ORG), [run[1] as object, run[3] as object], ownerOf, { known: knownFor(BOB), run, sessionId: ROOM, rowsBefore })
      expect(hiddenOf(out[0])).to.equal(undefined)
      expect(hiddenOf(out[1])).to.deep.equal(LOST)
      expect(rowsBefore.called).to.equal(false)
    })

    it('with its first turn unread, an earlier answer is unconfirmed and the one just made is its asker’s', async () => {
      const run = [bare(answer(2)), bare(answer(3, { _id: 'fresh' }))]
      const out = await redactWithTurns(appConfig, viewerOf(DANA, ORG), run, ownerOf, {
        known: settled([BOARD.id]),
        fresh: (m) => m._id === 'fresh',
      })
      expect(hiddenOf(out[0])).to.deep.equal(UNCONFIRMED)
      expect(hiddenOf(out[1])).to.equal(undefined)
    })

    it('a first turn the store has not placed by the read’s deadline is unread: its answer is unconfirmed, at the deadline', async () => {
      const known = knownFor(CAROL)
      const placesOf = sinon.stub().returns(new Promise(() => undefined))
      const [out] = await settlesAt(50, () => {
        known.deadline = Date.now() + 50
        return redactWithTurns(appConfig, viewerOf(CAROL, ORG), [bare(answer(2))], ownerOf, { known, sessionId: ROOM, rowsBefore: store([question(1)]), placesOf })
      })
      expect(hiddenOf(out)).to.deep.equal(UNCONFIRMED)
    })
  })

  describe('storedAnswerShown', () => {
    it('an answer the store has not read by the read’s deadline is not shown', async () => {
      const known = knownFor(CAROL)
      const shown = await settlesAt(50, () => {
        known.deadline = Date.now() + 50
        return storedAnswerShown(appConfig, viewerOf(CAROL, ORG), ROOM, 2, ownerOf, { known, rowsBefore: silent() })
      })
      expect(shown).to.equal(false)
    })
  })

  describe('withWholeTurns: what the reader check is given of a page', () => {
    const card = (seq: number): ViewerMessage & Record<string, unknown> => ({ _id: `c${seq}`, sessionId: ROOM, seq, messageType: 'tool_call', content: '', requestedBy: DANA })
    const ids = (rows: object[]) => rows.map((r) => (r as { _id: string })._id)

    it('puts the rows in chat order: a page of changed rows comes in the order they changed', async () => {
      const page = [question(10), card(11), answer(13), answer(8)]
      const rowsBefore = store([answer(8), plain(9), question(10), card(11), plain(12), answer(13)])
      const out = await withWholeTurns(page, ROOM, ORG, { rowsBefore })
      expect(ids(out)).to.deep.equal(['a8', 'q9', 'q10', 'c11', 'q12', 'a13'])
    })

    it('reads the rest of the turn of a card the page holds alone, up to the question after it', async () => {
      const rowsBefore = store([plain(1), answer(2), question(3), card(4), answer(5), plain(6), answer(7)])
      const out = await withWholeTurns([card(4)], ROOM, ORG, { rowsBefore })
      expect(ids(out)).to.deep.equal(['q3', 'c4', 'a5'])
    })

    it('asks the store nothing for a page that is the whole chat', async () => {
      const rowsBefore = store([])
      const out = await withWholeTurns([answer(2), question(1)], ROOM, ORG, { whole: true, rowsBefore })
      expect(ids(out)).to.deep.equal(['q1', 'a2'])
      expect(rowsBefore.called).to.equal(false)
    })

    it('asks the store nothing for a page whose rows are each judged alone: questions, notes, system lines', async () => {
      const rowsBefore = store([plain(1), answer(2), plain(3), note(4), line(5)])
      const out = await withWholeTurns([line(5), plain(3), note(4)], ROOM, ORG, { rowsBefore })
      expect(ids(out)).to.deep.equal(['q3', 'n4', 's5'])
      expect(rowsBefore.called).to.equal(false)
    })

    it('asks the store nothing for a page that holds an answer’s turn from its question', async () => {
      const rowsBefore = store([plain(1), answer(2), question(3), answer(4)])
      const out = await withWholeTurns([answer(4), question(3)], ROOM, ORG, { rowsBefore })
      expect(ids(out)).to.deep.equal(['q3', 'a4'])
      expect(rowsBefore.called).to.equal(false)
    })

    it('reads the question of an answer the page holds without it (control)', async () => {
      const rowsBefore = store([plain(1), answer(2), question(3), answer(4)])
      const out = await withWholeTurns([answer(4)], ROOM, ORG, { rowsBefore })
      expect(ids(out)).to.deep.equal(['q3', 'a4'])
      expect(rowsBefore.calledOnce).to.equal(true)
    })

    it('a reply to a card is read with the card the page leaves out', async () => {
      const reply = question(5, { _id: 'r5', content: 'Tool approval: yes' })
      const rowsBefore = store([question(3), card(4), reply, answer(6)])
      const out = await withWholeTurns([reply], ROOM, ORG, { rowsBefore })
      expect(ids(out)).to.deep.equal(['q3', 'c4', 'r5', 'a6'])
    })

    it('gives up a read the store has not answered by the read’s deadline: the page stays as it came, in chat order', async () => {
      const out = await settlesAt(50, () => withWholeTurns([card(4), question(3)], ROOM, ORG, { rowsBefore: silent(), deadline: Date.now() + 50 }))
      expect(ids(out)).to.deep.equal(['q3', 'c4'])
    })
  })
})
