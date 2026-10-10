import { expect } from 'chai'
import {
  CHAT_TITLE_MAX_LENGTH,
  displayTitle,
  titleFromQuery,
} from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.parser'

const AGENT = '6d9fb183-3e58-441e-ab1f-796f21e6da8f'
const EXAMPLE = `<@agent:${AGENT}> hi; <@assistant:self> Can you a create a agent that tells jokes`
const labels = (entries: Record<string, string>) => new Map(Object.entries(entries))

describe('titleFromQuery', () => {
  it("names the user's example agent", () => {
    expect(titleFromQuery(EXAMPLE, labels({ [`agent:${AGENT}`]: 'Joke Buddy' }))).to.equal(
      '@Joke Buddy hi; Can you a create a agent that tells jokes',
    )
  })

  it('drops an unnamed agent token and the leading punctuation it leaves', () => {
    expect(titleFromQuery(EXAMPLE)).to.equal('hi; Can you a create a agent that tells jokes')
    expect(titleFromQuery(`<@agent:${AGENT}> ; hi`)).to.equal('hi')
  })

  it('names users and teams, and keeps punctuation attached to the name', () => {
    const l = labels({ 'user:u1': 'Bob  Ray', 'team:t-1': 'Platform' })
    expect(titleFromQuery('Hey <@user:u1>, ask <@team:t-1>.', l)).to.equal('Hey @Bob Ray, ask @Platform.')
    expect(titleFromQuery('Hey <@user:u1>, ask <@team:t-1>.')).to.equal('Hey, ask.')
  })

  it('removes the assistant token even when a label is offered, and escaped tokens always', () => {
    expect(titleFromQuery('<@assistant:self> summarize', labels({ 'assistant:self': 'PipesHub' }))).to.equal('summarize')
    expect(titleFromQuery('see <\\@user:u1> and <@user:u1>', labels({ 'user:u1': 'Bob' }))).to.equal('see and @Bob')
  })

  it('cuts a long query on a word boundary and never splits a name or a token', () => {
    const l = labels({ 'user:u1': 'Joke Buddy Deluxe' })
    const long = `${'word '.repeat(18)}<@user:u1> ${'tail '.repeat(10)}`
    const title = titleFromQuery(long, l)
    expect(title.length).to.be.at.most(CHAT_TITLE_MAX_LENGTH)
    expect(title).to.not.include('<')
    expect(title).to.match(/(word|@Joke Buddy Deluxe)$/)
    const cutInName = titleFromQuery(`${'x'.repeat(88)} <@user:u1>`, l)
    expect(cutInName).to.equal('x'.repeat(88))
    expect(titleFromQuery('y'.repeat(150))).to.equal('y'.repeat(100))
  })

  it('is empty when only mentions were typed', () => {
    expect(titleFromQuery('<@assistant:self> <@user:u1>')).to.equal('')
    expect(titleFromQuery('   ')).to.equal('')
  })
})

describe('displayTitle', () => {
  it('cleans a stored token title without lookups', () => {
    expect(displayTitle(`<@agent:${AGENT}> hi; <@assistant:self> Can you a create a agent that tells jokes`)).to.equal(
      'hi; Can you a create a agent that tells jokes',
    )
    expect(displayTitle('ask <\\@user:u1> now')).to.equal('ask now')
  })

  it('drops half a token left by the old 100-character cut', () => {
    expect(displayTitle(`hello <@agent:${AGENT.slice(0, 12)}`)).to.equal('hello')
    expect(displayTitle('hello <@')).to.equal('hello')
  })

  it('leaves a clean title alone and passes nullish through', () => {
    expect(displayTitle('  a < b  and  c ')).to.equal('  a < b  and  c ')
    expect(displayTitle(undefined)).to.equal(undefined)
    expect(displayTitle(null)).to.equal(undefined)
    expect(displayTitle('<@assistant:self>')).to.equal('')
  })
})
