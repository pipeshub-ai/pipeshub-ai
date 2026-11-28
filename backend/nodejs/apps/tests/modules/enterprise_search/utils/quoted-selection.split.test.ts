import { expect } from 'chai'
import {
  QUOTED_SELECTION_FOOTER as FOOTER,
  QUOTED_SELECTION_HEADER as HEADER,
  splitQuotedSelection,
} from '../../../../src/modules/enterprise_search/utils/quoted-selection'

describe('splitQuotedSelection: a quoting message as anyone but its author may read it', () => {
  it('a message with no quote is its own words', () => {
    expect(splitQuotedSelection('What changed in Q3?')).to.deep.equal({ quoted: false, question: 'What changed in Q3?' })
  })

  it('a quote and a question: the question alone', () => {
    expect(splitQuotedSelection(`${HEADER}R says revenue grew.${FOOTER}Why?`)).to.deep.equal({ quoted: true, question: 'Why?' })
  })

  it('an excerpt that holds the closing mark ends at the last mark, so none of it reads as the question', () => {
    const excerpt = 'def plan():\n"""\nThe board deck\n"""\n\nreturn "R says revenue grew"'
    expect(splitQuotedSelection(`${HEADER}${excerpt}${FOOTER}Is this right?`)).to.deep.equal({ quoted: true, question: 'Is this right?' })
  })

  it('a question that types the mark loses its start for others, never the excerpt its end', () => {
    expect(splitQuotedSelection(`${HEADER}R says revenue grew.${FOOTER}Like this:${FOOTER}why?`).question).to.equal('why?')
  })

  it('a quote that never closes, a quote alone, and a quote cut short by a trim have no words of their own', () => {
    expect(splitQuotedSelection(`${HEADER}R says revenue grew.`)).to.deep.equal({ quoted: true, question: '' })
    expect(splitQuotedSelection(`${HEADER}R says revenue grew.${FOOTER}`)).to.deep.equal({ quoted: true, question: '' })
    expect(splitQuotedSelection(`${HEADER}R says revenue grew.${FOOTER}`.trimEnd())).to.deep.equal({ quoted: true, question: '' })
  })
})
