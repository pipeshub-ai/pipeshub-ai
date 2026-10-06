import { expect } from 'chai'
import { claimedMentions, inertUnlistedTokens, parseMentions, withoutMentionTokens } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.parser'
import { MENTIONS_MAX } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.types'
import {
  ASSISTANT_ALIASES,
  INERT_ALIASES,
  RESERVED_ALIASES,
} from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/alias-table'

const ID = '6abf35278cf29be1a243f001'

describe('mention parser (MN-01b, MN-04)', () => {
  for (const text of ['@PipesHub hi', '@assistant summarize', 'hey @agent', '@AI help', 'ok @Assistant, thanks', '@pipeshub.', '(@bot)']) {
    it(`infers the assistant alias in ${JSON.stringify(text)}`, () => {
      expect(parseMentions(text).assistantAlias).to.equal(true)
    })
  }

  for (const text of ['hello @bob', 'mail bob@agent.com', 'see @ai.com', '@assistants', '@everyone look', '@here', '@all', 'a/@assistant', 'plain text', '<@assistant:self> only a token']) {
    it(`does not infer an alias in ${JSON.stringify(text)}`, () => {
      expect(parseMentions(text).assistantAlias).to.equal(false)
    })
  }

  it('MN-01b: any other @word yields no mention at all', () => {
    const parsed = parseMentions('@carol please look, cc @sales-team')
    expect(parsed).to.deep.equal({ tokens: [], overflow: false, assistantAlias: false })
  })

  it('extracts unescaped tokens of every type once, in order', () => {
    const parsed = parseMentions(`<@user:${ID}> and <@team:t-1> and <@agent:a_1> and <@assistant:self> and again <@user:${ID}>`)
    expect(parsed.tokens).to.deep.equal([
      { type: 'user', id: ID },
      { type: 'team', id: 't-1' },
      { type: 'agent', id: 'a_1' },
      { type: 'assistant', id: 'self' },
    ])
  })

  it('MN-04: an escaped literal stays inert and cannot smuggle an alias either', () => {
    expect(parseMentions('<\\@agent:xyz>')).to.deep.equal({ tokens: [], overflow: false, assistantAlias: false })
    expect(parseMentions('<@agent:xyz>').tokens).to.deep.equal([{ type: 'agent', id: 'xyz' }])
  })

  it('ignores a token type it does not know and ids with markup', () => {
    expect(parseMentions('<@admin:1> <@user:a b> <@user:<x>>').tokens).to.deep.equal([])
  })

  it('caps the tokens at the maximum and says it overflowed', () => {
    const text = Array.from({ length: MENTIONS_MAX + 3 }, (_, i) => `<@user:u${String(i)}>`).join(' ')
    const parsed = parseMentions(text)
    expect(parsed.tokens).to.have.length(MENTIONS_MAX)
    expect(parsed.overflow).to.equal(true)
  })

  it('reserved aliases: the assistant forms act, everyone/here/all are reserved but inert', () => {
    expect(ASSISTANT_ALIASES).to.include.members(['pipeshub', 'assistant', 'agent', 'ai'])
    expect(INERT_ALIASES).to.deep.equal(['everyone', 'here', 'all'])
    expect([...RESERVED_ALIASES].sort()).to.deep.equal(['agent', 'ai', 'all', 'assistant', 'bot', 'everyone', 'here', 'pipeshub'])
  })

  it('inertUnlistedTokens keeps listed tokens and escapes the rest, so an escaped one stays inert on a second pass', () => {
    const text = `<@user:${ID}> <@team:t1> <\\@agent:x> <@assistant:self>`
    const out = inertUnlistedTokens(text, [{ type: 'user', id: ID }, { type: 'assistant', id: 'self' }])
    expect(out).to.equal(`<@user:${ID}> <\\@team:t1> <\\@agent:x> <@assistant:self>`)
    expect(inertUnlistedTokens(out, [])).to.equal(`<\\@user:${ID}> <\\@team:t1> <\\@agent:x> <\\@assistant:self>`)
    expect(parseMentions(inertUnlistedTokens(text, [])).tokens).to.deep.equal([])
  })

  it('claimedMentions adds the assistant for a typed alias, de-duplicates and caps', () => {
    expect(claimedMentions('@ai hi', [{ type: 'assistant', id: 'self' }])).to.deep.equal([{ type: 'assistant', id: 'self' }])
    expect(claimedMentions('hi', undefined)).to.deep.equal([])
    const many = Array.from({ length: MENTIONS_MAX }, (_, i) => ({ type: 'team' as const, id: `t${String(i)}` }))
    expect(claimedMentions('@assistant hi', many)).to.have.length(MENTIONS_MAX)
  })

  it('withoutMentionTokens blanks live and escaped tokens only, so a real tag still reaches the XSS filter', () => {
    expect(withoutMentionTokens(`hi <@user:${ID}> and <\\@team:t1>!`)).to.equal('hi   and  !')
    expect(withoutMentionTokens('<b onclick=x>')).to.equal('<b onclick=x>')
  })
})
