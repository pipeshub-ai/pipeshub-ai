import { expect } from 'chai'
import { parseMentions, ASSISTANT_MENTION } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.parser'
import { classifyResponder } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/responder-router'

// `@assistant help` is an ordinary assistant mention: Node forwards it to Python, which answers with the capability card.
describe('MN-20: `@assistant help` classification', () => {
  for (const text of ['@assistant help', '@PipesHub help', '<@assistant:self> help']) {
    it(`"${text}" addresses the assistant and is never a note`, () => {
      const parsed = parseMentions(text)
      const mentions = parsed.tokens.length > 0 ? [...parsed.tokens] : parsed.assistantAlias ? [ASSISTANT_MENTION] : []
      expect(mentions.map((m) => m.type)).to.deep.equal(['assistant'])
      for (const respondMode of ['smart', 'mention_only', 'always'] as const) {
        expect(classifyResponder({ mentions, respondMode, sessionKind: 'chat' }), respondMode).to.equal('assistant')
      }
    })
  }
})
