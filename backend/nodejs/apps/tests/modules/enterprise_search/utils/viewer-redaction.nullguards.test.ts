import { expect } from 'chai'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
  collectMessageRecordIds,
  collectSearchRecordIds,
  redactConversationFor,
  redactMessagesForViewer,
  redactSearchForViewer,
  readableRecordIds,
} from '../../../../src/modules/enterprise_search/utils/viewer-redaction'

const doc = { _id: 'c1', chunkIndex: 1, metadata: { recordId: 'r1', origin: 'UPLOAD' } }
const msg = (citations: any[]): any => ({ messageType: 'bot_response', content: 'x [1]', citations })

describe('viewer redaction tolerates missing citations and access results', () => {
  afterEach(() => sinon.restore())

  it('deleted citation (populated citationId null) does not throw', () => {
    const m = msg([{ citationId: null }, { citationId: doc }])
    expect(() => collectMessageRecordIds([m])).to.not.throw()
    expect(() => redactMessagesForViewer([m], new Set())).to.not.throw()
    expect(JSON.stringify(redactMessagesForViewer([m], new Set()))).to.not.include('metadata')
  })
  it('citationData null does not throw', () => {
    expect(() => redactMessagesForViewer([msg([{ citationData: null, citationId: null }])], new Set())).to.not.throw()
  })
  it('null array element in citations (corrupt/legacy) does not throw', () => {
    expect(() => collectMessageRecordIds([msg([null, { citationId: doc }])])).to.not.throw()
    expect(() => redactMessagesForViewer([msg([null, { citationId: doc }])], new Set())).to.not.throw()
  })
  it('saved search with null / bare hits does not throw', () => {
    const s: any = { citationIds: [null, 'bare', doc], records: {} }
    expect(() => collectSearchRecordIds(s)).to.not.throw()
    const out: any = redactSearchForViewer(s, new Set())
    expect(JSON.stringify(out)).to.not.include('UPLOAD')
    expect(out.citationIds.every((hit: any) => hit === null || hit.redacted === true)).to.equal(true)
  })
  it('access check answering JSON null fails closed (no throw, nothing readable)', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: null } as any)
    const cfg: any = { jwtSecret: 's', scopedJwtSecret: 's2', aiBackend: 'http://ai' }
    expect((await readableRecordIds(cfg, { userId: 'u', orgId: 'o' }, ['r1'])).size).to.equal(0)
  })
  it('one null batch must not discard ids already found in earlier batches', async () => {
    const cfg: any = { jwtSecret: 's', scopedJwtSecret: 's2', aiBackend: 'http://ai' }
    const ids = Array.from({ length: 300 }, (_, i) => `r${i}`)
    const ex = sinon.stub(AIServiceCommand.prototype, 'execute')
    ex.onFirstCall().resolves({ statusCode: 200, data: { accessible: ['r0'] } } as any)
    ex.onSecondCall().resolves({ statusCode: 200, data: null } as any)
    ex.resolves({ statusCode: 200, data: { accessible: [] } } as any)
    const out = await readableRecordIds(cfg, { userId: 'u', orgId: 'o' }, ids)
    expect(out.has('r0')).to.equal(true)
  })
  it('a conversation without an initiator is redacted, failing closed: an unstamped turn is not the viewer’s', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { accessible: [] } } as any)
    const cfg: any = { jwtSecret: 's', scopedJwtSecret: 's2', aiBackend: 'http://ai' }
    const own: any = { ...msg([]), content: 'mine', authorUserId: 'u' }
    const unknown: any = { messageType: 'bot_response', content: 'SECRET [1]', citations: [{ citationId: doc }] }
    const conv: any = { messages: [own, unknown] }
    const out: any = await redactConversationFor(cfg, conv, { userId: 'u', orgId: 'o' })
    expect(out.messages[0].content).to.equal('mine')
    expect(out.messages[1].redaction).to.include({ redacted: true })
    expect(JSON.stringify(out.messages[1])).to.not.include('SECRET')
  })
})
