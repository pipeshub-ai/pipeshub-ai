import { expect } from 'chai'
import { OPAQUE_SOURCE, redactMessagesFor, redactMessagesForViewer } from '../../../../src/modules/enterprise_search/utils/viewer-redaction'

const SECRET = 'Q3 revenue was 4.2M according to the board deck'
const answer = (extra: Record<string, any> = {}) => ({
  _id: 'm1',
  messageType: 'bot_response',
  content: SECRET,
  citations: [],
  followUpQuestions: [{ question: `Why was ${SECRET}?` }],
  tools: [{ toolName: 't', toolResult: SECRET }],
  ...extra,
})
const hidden = (m: Record<string, any>) => m.redaction?.redacted === true && m.content === ''
const view = (message: Record<string, any>, readable: string[] = []) => redactMessagesForViewer([message], new Set(readable))[0]!

describe('another person’s answer, judged by the records its turn drew on', () => {
  it('is hidden when no list was stored (older answers): unknown sources fail closed', () => {
    expect(hidden(view(answer()))).to.equal(true)
  })

  it('is shown when the stored list is empty: provably no sources', () => {
    const out = view(answer({ sourceRecordIds: [] }))
    expect(out.content).to.equal(SECRET)
    expect(out.redaction).to.equal(undefined)
  })

  it('is shown when the viewer can read every record, and the list is never returned', () => {
    const out = view(answer({ sourceRecordIds: ['r1', 'r2'] }), ['r1', 'r2'])
    expect(out.content).to.equal(SECRET)
    expect(JSON.stringify(out)).to.not.include('sourceRecordIds')
    expect(JSON.stringify(out)).to.not.include('r1')
  })

  it('is hidden when a retrieved record is unreadable, cited or not (prefetch with no tool call)', () => {
    const out = view(answer({ sourceRecordIds: ['r1', 'r2'] }), ['r1'])
    expect(hidden(out)).to.equal(true)
    expect(JSON.stringify(out)).to.not.include('r2')
  })

  it('is hidden when the output has no record ids (connector, web, MCP, sub-agent)', () => {
    expect(hidden(view(answer({ sourceRecordIds: [OPAQUE_SOURCE] }), [OPAQUE_SOURCE]))).to.equal(true)
  })

  it('drops follow-up questions and details from a hidden answer', () => {
    const out = view(answer())
    expect(out.followUpQuestions).to.deep.equal([])
    expect(out.detailsHidden).to.equal(true)
    expect(JSON.stringify(out)).to.not.include('Q3 revenue')
  })

  it('keeps only readable-cited spans when an unreadable record was retrieved but not cited', () => {
    const doc = { _id: 'c1', chunkIndex: 1, content: 'x', metadata: { recordId: 'r1', webUrl: 'https://app/record/r1' } }
    const out = view(
      answer({
        content: `Open fact [1](https://app/record/r1). Connective ${SECRET}.`,
        citations: [{ citationId: doc }],
        sourceRecordIds: ['r1', 'r2'],
      }),
      ['r1'],
    )
    expect(out.content).to.include('Open fact')
    expect(out.content).to.not.include('Q3 revenue')
    expect(out.redaction.partial).to.equal(true)
  })

  it('leaves a cited answer with no stored list as the citations judge it', () => {
    const doc = { _id: 'c1', chunkIndex: 1, content: 'x', metadata: { recordId: 'r1', webUrl: 'https://app/record/r1' } }
    const out = view(answer({ content: `Fact [1](https://app/record/r1).`, citations: [{ citationId: doc }] }), ['r1'])
    expect(out.content).to.equal('Fact [1](https://app/record/r1).')
  })
})

describe('the source list is never returned', () => {
  it('not even in the author’s own turns', async () => {
    const own = answer({ sourceRecordIds: ['r1'], authorUserId: 'u1' })
    const [out] = await redactMessagesFor({} as any, [own], 'u1', { userId: 'u1', orgId: 'o1' })
    expect(out).to.not.have.property('sourceRecordIds')
    expect(out!.content).to.equal(SECRET)
  })
})
