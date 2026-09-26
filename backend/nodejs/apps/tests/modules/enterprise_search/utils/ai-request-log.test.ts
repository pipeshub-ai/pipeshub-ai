import { expect } from 'chai'
import { buildAiRequestLogMetadata } from '../../../../src/modules/enterprise_search/utils/ai-request-log'

const SECRET = 'quarterly layoffs plan for team phoenix'

describe('buildAiRequestLogMetadata', () => {
  it('reports sizes and counts, never the query, history or attachment content', () => {
    const meta = buildAiRequestLogMetadata({
      query: SECRET,
      previousConversations: [
        { role: 'user_query', content: `${SECRET} earlier` },
        { role: 'bot_response', content: 'an answer' },
      ],
      attachments: [{ recordName: `${SECRET}.pdf` }],
      filters: { apps: ['a1', 'a2'], kb: ['k1'] },
      tools: ['jira.search'],
      chatMode: 'quick',
      modelKey: 'model-1',
      currentUser: { email: 'ceo@example.com' },
    })

    expect(JSON.stringify(meta)).to.not.contain('phoenix')
    expect(JSON.stringify(meta)).to.not.contain('ceo@example.com')
    expect(meta).to.deep.include({
      queryLength: SECRET.length,
      previousConversationCount: 2,
      attachmentCount: 1,
      filterAppCount: 2,
      filterKbCount: 1,
      toolCount: 1,
      chatMode: 'quick',
      modelKey: 'model-1',
    })
    expect(meta.previousConversationChars).to.equal(`${SECRET} earlier`.length + 'an answer'.length)
  })

  it('tolerates missing and malformed fields', () => {
    const meta = buildAiRequestLogMetadata({ query: 42, previousConversations: 'x', filters: null })
    expect(meta).to.deep.include({ queryLength: 0, previousConversationCount: 0, attachmentCount: 0 })
  })
})
