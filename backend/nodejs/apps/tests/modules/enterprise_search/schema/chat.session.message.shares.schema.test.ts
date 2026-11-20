import 'reflect-metadata'
import { expect } from 'chai'
import { Types } from 'mongoose'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { ANSWER_SHARE_SCOPES, SHARE_REQUEST_STATES } from '../../../../src/modules/enterprise_search/schema/answer-share.schema'

const oid = () => new Types.ObjectId()
const row = (extra: Record<string, unknown> = {}) =>
  new ChatSessionMessage({ sessionId: oid(), orgId: oid(), seq: 1, messageType: 'bot_response', content: 'An answer.', ...extra })

// The run stamp and an answer's shares are written by the turn code and the conversation repository, so the message
// schema keeps them: a field it does not declare is dropped on write.
describe('enterprise_search/schema/chat.session.message.schema: the run stamp and answer shares', () => {
  it('keeps the run that wrote a row', () => {
    const doc = row({ runId: 'run-1' })
    expect(doc.validateSync()).to.equal(undefined)
    expect(doc.toObject()).to.include({ runId: 'run-1' })
  })

  it("keeps an answer's consent, whom it covers and the asks to see it", () => {
    const by = oid()
    const at = new Date('2026-11-20T00:00:00Z')
    const doc = row({
      consent: { by, at, ver: 'v1', scope: 'people' },
      consentTo: [{ userId: by, memberSince: 'm1' }],
      shareRequests: [{ by, at, ver: 'v1', state: 'open' }],
    })
    expect(doc.validateSync()).to.equal(undefined)
    const o = doc.toObject() as unknown as Record<string, unknown>
    expect(o.consent).to.deep.equal({ by, at, ver: 'v1', scope: 'people' })
    expect(o.consentTo).to.deep.equal([{ userId: by, memberSince: 'm1' }])
    expect(o.shareRequests).to.deep.equal([{ by, at, ver: 'v1', state: 'open' }])
  })

  it('refuses a consent or an ask with an unknown scope or state, or without its version', () => {
    const by = oid()
    const at = new Date()
    expect(row({ consent: { by, at, ver: 'v1', scope: 'outside' } }).validateSync()).to.not.equal(undefined)
    expect(row({ consent: { by, at, scope: 'everyone' } }).validateSync()).to.not.equal(undefined)
    expect(row({ shareRequests: [{ by, at, ver: 'v1', state: 'maybe' }] }).validateSync()).to.not.equal(undefined)
    expect([...ANSWER_SHARE_SCOPES]).to.deep.equal(['everyone', 'people'])
    expect([...SHARE_REQUEST_STATES]).to.deep.equal(['open', 'shared', 'dismissed', 'void'])
  })

  it('leaves them out of a plain row, and keeps whom a share covers and the asks off every read', () => {
    const o = row().toObject()
    for (const k of ['runId', 'consent', 'consentTo', 'shareRequests']) expect(o, k).to.not.have.property(k)
    expect(ChatSessionMessage.schema.path('consentTo').options.select).to.equal(false)
    expect(ChatSessionMessage.schema.path('shareRequests').options.select).to.equal(false)
  })

  it("reads a chat's shared answers, current and archived, from their partial indexes", () => {
    const indexes = ChatSessionMessage.schema.indexes()
    const has = (keys: Record<string, number>, filter: Record<string, unknown>) =>
      indexes.some(([k, o]) => JSON.stringify(k) === JSON.stringify(keys) && JSON.stringify(o?.partialFilterExpression) === JSON.stringify(filter))
    expect(has({ sessionId: 1, 'consent.ver': 1 }, { consent: { $type: 'object' } })).to.equal(true)
    expect(has({ sessionId: 1, 'labelHistory.ver': 1 }, { 'labelHistory.consent': { $type: 'object' } })).to.equal(true)
  })
})
