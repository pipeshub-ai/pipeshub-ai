import { expect } from 'chai'
import { Types } from 'mongoose'
import { includableRows, resetVisibility } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { answersOnlyTurns, givesEarlierResults } from '../../../../../../src/modules/enterprise_search/utils/earlier-turns'
import { stampTurnRow } from '../../../../../../src/modules/enterprise_search/services/conversation/turn/turn-run'
import type { IMessage } from '../../../../../../src/modules/enterprise_search/types/conversation.interfaces'
import { CAROL, DANA, ORG, ROOM, T1, answer, nextId, room, world, type Row } from './share-world'

/**
 * What a turn is given of what earlier turns' tools returned, and the answers given it, in a build whose answers carry no
 * label. A turn run while nobody else read the chat is given the earlier tool results and reference data; nothing names
 * the records those came from, so the rows it writes are marked. Another person's marked answer then reaches neither a
 * reader nor someone else's later run, and every answer after it goes the same way, as the reader's view hides it.
 */

const SEARCH = { tool_name: 'search', result: 'Alpha budget 4.2M' }
const ASK = { tool_name: 'internaltools__ask_user_question', result: '{"answers":["EU"]}' }

describe('the earlier tool results a turn is given', () => {
  it('are what tools returned and the reference data they found, never the cards the person answered', () => {
    expect(givesEarlierResults([{ role: 'bot_response', content: 'a', tool_results: [SEARCH] }])).to.equal(true)
    expect(givesEarlierResults([{ role: 'bot_response', content: 'a', referenceData: [{ name: 'Alpha plan', id: 'a1' }] }])).to.equal(true)
    expect(givesEarlierResults([{ role: 'bot_response', content: 'a', tool_results: [ASK] }])).to.equal(false)
    expect(givesEarlierResults([{ role: 'bot_response', content: 'a', referenceData: [] }])).to.equal(false)
    expect(givesEarlierResults([])).to.equal(false)
  })

  it('are none of the turns a chat others read gives', () => {
    const turns = [
      { role: 'user_query', content: 'what do our plans say?' },
      { role: 'bot_response', content: 'Charlie lists three themes.', tool_results: [SEARCH, ASK], referenceData: [{ name: 'Alpha plan' }] },
    ]
    expect(givesEarlierResults(answersOnlyTurns(turns))).to.equal(false)
  })

  it('mark every row the turn given them writes, and no row of any other turn', () => {
    const row = { messageType: 'bot_response', content: 'It is 4.2M.' } as unknown as IMessage
    const asker = new Types.ObjectId()
    expect(stampTurnRow(row, { requestedBy: asker, earlierResults: true })).to.include({ earlierResults: true, requestedBy: asker })
    expect(stampTurnRow(row, { requestedBy: asker })).to.not.have.property('earlierResults')
  })
})

describe("someone else's later run, in a build whose answers carry no label", () => {
  beforeEach(() => resetVisibility())
  afterEach(() => resetVisibility())

  const question = (content: string, by: string): Row => ({
    _id: nextId(),
    sessionId: ROOM,
    orgId: ORG,
    messageType: 'user_query',
    content,
    requestedBy: by,
    createdAt: T1,
  })
  /** An answer of Dana's as this build stores it: no label. */
  const unlabelled = (content: string, more: Record<string, unknown> = {}): Row => answer(content, DANA, { provenance: undefined, ...more })

  /** Dana's turns: the first searched; the second was written from that search; the third followed it. */
  const chat = (): Row[] => [
    question('What do our plans say?', DANA),
    unlabelled('The public Charlie plan lists three themes.'),
    question('And the Alpha one?', DANA),
    unlabelled('Alpha budget 4.2M.', { earlierResults: true }),
    question('Thanks. Anything else?', DANA),
    unlabelled('That is all I found.'),
  ]

  /** The row as one stored before the mark was recorded. */
  const unmarked = (row: Row): Row => {
    const old = { ...row }
    delete old.earlierResults
    return old
  }

  const historyFor = async (who: string, rows: Row[]): Promise<string[]> => {
    const w = world(rows)
    const sent = await includableRows({ userId: who, orgId: ORG, member: true }, room, rows, {
      deps: { ...w.deps, labelRule: async () => false },
      member: true,
    })
    return sent.filter((r) => (r as Row).messageType === 'bot_response').map((r) => String((r as Row).content))
  }

  it("is given none of an answer Dana's run wrote from what earlier tools returned, and none of what follows it", async () => {
    expect(await historyFor(CAROL, chat())).to.deep.equal(['The public Charlie plan lists three themes.', '', ''])
  })

  it('her own next run is given them all', async () => {
    expect(await historyFor(DANA, chat())).to.deep.equal([
      'The public Charlie plan lists three themes.',
      'Alpha budget 4.2M.',
      'That is all I found.',
    ])
  })

  it('is given nothing of what follows an answer its own filter emptied, which went to every later run as history', async () => {
    const rows = chat().map(unmarked)
    // The history filter emptied Dana's first answer for Carol: it used a record she can't open.
    rows[1] = { ...rows[1], content: '', answerHidden: { redacted: true, reason: 'no_longer_accessible' } } as Row
    expect(await historyFor(CAROL, rows)).to.deep.equal(['', '', ''])
  })

  it('an answer stored before the mark was recorded goes as before, by what its own filter left of it', async () => {
    const old = chat().map(unmarked)
    expect(await historyFor(CAROL, old)).to.deep.equal([
      'The public Charlie plan lists three themes.',
      'Alpha budget 4.2M.',
      'That is all I found.',
    ])
  })
})
