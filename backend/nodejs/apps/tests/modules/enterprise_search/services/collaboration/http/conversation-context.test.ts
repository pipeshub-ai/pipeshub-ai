import 'reflect-metadata'
import { expect } from 'chai'
import { InternalServerError } from '../../../../../../src/libs/errors/http.errors'
import {
  conversationContextOf,
  setConversationContext,
} from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'

const ctx = (userId: string) => ({ caller: { userId, orgId: 'o', teamIds: [] as string[] } })

describe('conversation request context', () => {
  it('PH04-02: throws InternalServerError when no guard ran', () => {
    expect(() => conversationContextOf({} as any)).to.throw(InternalServerError, 'conversation guard not mounted')
  })

  it('PH04-02: returns the stored object, and contexts do not leak across requests', () => {
    const a: any = {}
    const b: any = {}
    const stored = ctx('a')
    setConversationContext(a, stored)
    expect(conversationContextOf(a)).to.equal(stored)
    expect(() => conversationContextOf(b)).to.throw(InternalServerError)
    setConversationContext(b, ctx('b'))
    expect(conversationContextOf(a).caller.userId).to.equal('a')
    expect(conversationContextOf(b).caller.userId).to.equal('b')
  })
})
