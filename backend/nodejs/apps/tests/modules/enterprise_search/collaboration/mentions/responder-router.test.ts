import { expect } from 'chai'
import { classifyResponder } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/responder-router'
import cases from '../../../../fixtures/respond-mode-cases.json'

describe('classifyResponder (MN-08, MN-09, PH10-07)', () => {
  it('runs every row of the table shared with the composer', () => {
    expect(cases.length).to.be.greaterThan(30)
    for (const row of cases) {
      const got = classifyResponder({ mentions: row.mentions as never, respondMode: row.respondMode as never, sessionKind: row.sessionKind as never })
      expect(got, row.name).to.equal(row.expected)
    }
  })

  it('MN-08: smart with no mentions is answered', () => {
    expect(classifyResponder({ mentions: [], respondMode: 'smart', sessionKind: 'chat' })).to.equal('assistant')
  })

  it('MN-09: mention_only with no mentions is a note', () => {
    expect(classifyResponder({ mentions: [], respondMode: 'mention_only', sessionKind: 'chat' })).to.equal('note')
  })

  it('every case is covered for both session kinds and all three modes', () => {
    const seen = new Set(cases.map((c) => `${c.respondMode}/${c.sessionKind}`))
    expect(seen.size).to.equal(6)
    expect(new Set(cases.map((c) => c.expected))).to.deep.equal(new Set(['assistant', 'own_agent', 'note']))
  })
})
