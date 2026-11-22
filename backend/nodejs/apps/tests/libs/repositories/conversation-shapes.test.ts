import 'reflect-metadata'
import { expect } from 'chai'
import { shapeAttachment, shapeCitationRef, shapeFollowUp, shapeReasoning, shapeReference, shapeTool } from '../../../src/libs/repositories/conversation/pg-schema'
import { assertOrg, dateOf } from '../../../src/libs/repositories/pg-util'

describe('the shapes a chat message keeps in a row', () => {
  it('each keeps only the fields the application\'s schema has', () => {
    expect(shapeCitationRef({ citationId: 'c', relevanceScore: 0.5, excerpt: 'e', context: 'x', extra: 1 })).to.deep.equal({ citationId: 'c', relevanceScore: 0.5, excerpt: 'e', context: 'x' })
    expect(shapeFollowUp({ question: 'q', confidence: 'high', reasoning: 'r', extra: 1 })).to.deep.equal({ question: 'q', confidence: 'high', reasoning: 'r' })
    expect(shapeAttachment({ recordId: 'r', recordName: 'n', mimeType: 'm', extension: 'e', virtualRecordId: 'v', source: 's', extra: 1 })).to.deep.equal({ recordId: 'r', recordName: 'n', mimeType: 'm', extension: 'e', virtualRecordId: 'v', source: 's' })
    expect(shapeReference({ name: 'n', id: 'i', type: 't', app: 'a', webUrl: 'u', metadata: { k: 1 }, extra: 1 })).to.deep.equal({ name: 'n', id: 'i', type: 't', app: 'a', webUrl: 'u', metadata: { k: 1 } })
    expect(shapeTool({ toolName: 't', toolResult: { ok: true }, extra: 1 })).to.deep.equal({ toolName: 't', toolResult: { ok: true } })
    expect(shapeReasoning({ messageId: 'm', turnIndex: 2, content: 'c', extra: 1 })).to.deep.equal({ messageId: 'm', turnIndex: 2, content: 'c' })
  })

  it('a value that is not an object gives an empty shape, not an error', () => {
    for (const f of [shapeCitationRef, shapeFollowUp, shapeAttachment, shapeReference, shapeTool, shapeReasoning])
      expect(f(null)).to.deep.equal({})
  })
})

describe('small helpers of the PostgreSQL repositories', () => {
  it('a time in milliseconds becomes a date, and nothing stays nothing', () => {
    expect(dateOf(0)?.toISOString()).to.equal('1970-01-01T00:00:00.000Z')
    expect(dateOf(undefined)).to.equal(null)
    expect(dateOf(null)).to.equal(null)
  })

  it('the organisation of a call must be the one the unit runs as', () => {
    expect(() => assertOrg({ orgId: 'a' } as never, 'a')).to.not.throw()
    expect(() => assertOrg({ orgId: 'a' } as never, 'b')).to.throw('differs from the one the unit of work runs as')
  })
})
