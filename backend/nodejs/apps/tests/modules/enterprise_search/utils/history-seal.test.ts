import { expect } from 'chai'
import { formatPreviousConversations } from '../../../../src/modules/enterprise_search/utils/utils'

/**
 * The seal on each history row (`_ref`, `_ver`, `_pub`) is what the AI backend names as the earlier answers a new
 * answer was given. A row the model is given nothing of must carry none, or the new answer inherits it: Dana's own
 * answer would be hidden from her because of Bob's answer she was never shown.
 */

const BOB_ANSWER = '650000000000000000000b01'
const DANA_ANSWER = '650000000000000000000d01'
const SEAL = ['_ref', '_ver', '_pub']

const toolPart = { type: 'tool_call', toolCallId: 'call-1', toolName: 'jira__search_issues', args: '{}', status: 'completed', resultPreview: 'AB-12 open' }
const askCard = { messageType: 'tool_call', content: '', tools: [{ toolName: 'ask_user_question', toolResult: { questions: [{ question: 'Which region?' }] } }] }

describe('the seal on the history sent to the AI backend', () => {
  describe('a row a check withheld', () => {
    it('goes with its mark and no seal, though it keeps its id and version', () => {
      const [, sent] = formatPreviousConversations([
        { messageType: 'user_query', content: 'What does my file say?' },
        { _id: BOB_ANSWER, labelVer: 'v-bob', messageType: 'bot_response', content: '', _withheld: true },
      ] as never)

      expect(sent).to.include({ role: 'bot_response', content: '', _withheld: true })
      expect(sent).to.not.have.any.keys(...SEAL)
    })

    it('gives nothing, even what a stale copy of it still holds: no text, tool data, references or files', () => {
      const [, sent] = formatPreviousConversations([
        { messageType: 'user_query', content: 'What does my file say?' },
        {
          _id: BOB_ANSWER,
          labelVer: 'v-bob',
          messageType: 'bot_response',
          content: 'BOB-ONLY text',
          parts: [toolPart],
          referenceData: [{ name: 'BOB-ONLY ref' }],
          attachments: [{ recordId: 'rec-b', recordName: 'bob-only.pdf' }],
          _ref: BOB_ANSWER,
          _ver: 'v-bob',
          _withheld: true,
        },
      ] as never)

      expect(sent).to.deep.equal({ role: 'bot_response', content: '', _withheld: true })
    })

    it("the history filter's hidden answer is withheld too", () => {
      const [sent] = formatPreviousConversations([
        { _id: BOB_ANSWER, labelVer: 'v-bob', messageType: 'bot_response', content: '', answerHidden: { redacted: true, reason: 'no_longer_accessible' } },
      ] as never)

      expect(sent).to.include({ content: '', _withheld: true })
      expect(sent).to.not.have.any.keys(...SEAL)
    })

    it("is not given the card of its turn either", () => {
      const sent = formatPreviousConversations([
        { messageType: 'user_query', content: 'Pick one' },
        askCard,
        { _id: BOB_ANSWER, labelVer: 'v-bob', messageType: 'bot_response', content: '', _withheld: true },
      ] as never)

      expect(sent[1]).to.not.have.property('tool_results')
      expect(sent[1]).to.not.have.any.keys(...SEAL)
    })
  })

  describe('an answer the model is given nothing of', () => {
    for (const [what, content] of [['no text', ''], ['only white space', '  \n ']] as const) {
      it(`with ${what} and no tool data names nothing`, () => {
        const [sent] = formatPreviousConversations([
          { _id: DANA_ANSWER, labelVer: 'v-dana', messageType: 'bot_response', content, status: 'stopped' },
        ] as never)

        expect(sent).to.not.have.any.keys(...SEAL, '_withheld')
      })
    }
  })

  describe('a row the model is given', () => {
    it('is sealed with its row and the version of its text', () => {
      const [sent] = formatPreviousConversations([
        { _id: DANA_ANSWER, labelVer: 'v-dana', messageType: 'bot_response', content: 'R says revenue grew.' },
      ] as never)

      expect(sent).to.include({ _ref: DANA_ANSWER, _ver: 'v-dana', _pub: false })
    })

    it("an answer that was only a card is sealed by its tool data", () => {
      const [, sent] = formatPreviousConversations([
        { messageType: 'user_query', content: 'Pick one' },
        { _id: DANA_ANSWER, labelVer: 'v-dana', messageType: 'bot_response', content: '', parts: [toolPart] },
      ] as never)

      expect(sent).to.include({ _ref: DANA_ANSWER, _ver: 'v-dana' })
      expect(sent).to.have.property('tool_results')
    })

    it('a question keeps its seal, and a row written before versions is legacy', () => {
      const [sent] = formatPreviousConversations([{ _id: DANA_ANSWER, messageType: 'user_query', content: '' }] as never)

      expect(sent).to.include({ _ref: DANA_ANSWER, _ver: 'legacy' })
    })

    it("keeps the reader check's mark of an answer the sender reads only through a share, and adds none itself", () => {
      const [shared, own] = formatPreviousConversations([
        { _id: BOB_ANSWER, messageType: 'bot_response', content: 'Bob: R2 says so.', _ref: BOB_ANSWER, _ver: 'v-bob', _pub: false, _viaShare: true },
        { _id: DANA_ANSWER, labelVer: 'v-dana', messageType: 'bot_response', content: 'R says revenue grew.', _viaShare: 'yes' },
      ] as never)

      expect(shared).to.include({ _ref: BOB_ANSWER, _ver: 'v-bob', _pub: false, _viaShare: true })
      expect(own).to.not.have.property('_viaShare')
    })
  })
})
