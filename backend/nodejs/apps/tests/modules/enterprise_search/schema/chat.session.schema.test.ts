import { expect } from 'chai'
import { Types } from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'

describe('enterprise_search/schema/chat.session.schema: isPinned', () => {
  const newSession = (fields: Record<string, unknown> = {}) =>
    new ChatSession({
      userId: new Types.ObjectId(),
      orgId: new Types.ObjectId(),
      initiator: new Types.ObjectId(),
      title: 'a chat',
      ...fields,
    })

  it('is an optional boolean', () => {
    const path = ChatSession.schema.path('isPinned')

    expect(path).to.exist
    expect(path.instance).to.equal('Boolean')
    expect(path.isRequired).to.not.equal(true)
  })

  // A stored `false` would sort ahead of chats that have no field at all in a
  // descending sort, and scramble the recency order of every unpinned chat.
  it('has no default, so a new or older chat is missing the field rather than false', () => {
    expect(ChatSession.schema.path('isPinned').options.default).to.equal(undefined)
    expect(newSession().toObject()).to.not.have.property('isPinned')
    expect(newSession().validateSync()).to.equal(undefined)
  })

  it('keeps a pin when one is set', () => {
    const session = newSession({ isPinned: true })

    expect(session.isPinned).to.equal(true)
    expect(session.toObject().isPinned).to.equal(true)
    expect(session.validateSync()).to.equal(undefined)
  })
})
