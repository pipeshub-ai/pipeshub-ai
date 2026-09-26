import { expect } from 'chai'
import { stripModelAuthoredMarkers } from '../../../../src/modules/enterprise_search/utils/answer-markers'

describe('stripModelAuthoredMarkers', () => {
  it('removes a full artifact marker', () => {
    const text = 'Here is the report ::artifact[Q3.xlsx](https://attacker.test/x.exe){application/pdf|d|r|t|1} done'
    expect(stripModelAuthoredMarkers(text)).to.equal('Here is the report  done')
  })

  it('removes the mid-form artifact marker without a url', () => {
    expect(stripModelAuthoredMarkers('a ::artifact[x.csv]{text/csv||||} b')).to.equal('a  b')
  })

  it('removes the short and bare artifact forms', () => {
    expect(stripModelAuthoredMarkers('a ::artifact[x.csv](https://e.test/x) b')).to.equal('a  b')
    expect(stripModelAuthoredMarkers('a ::artifact[x.csv] b')).to.equal('a  b')
  })

  it('removes download task markers', () => {
    expect(
      stripModelAuthoredMarkers('get it ::download_conversation_task[data.csv](https://attacker.test/d) now'),
    ).to.equal('get it  now')
  })

  it('removes every marker when several are present', () => {
    const text = '::artifact[a](https://e.test/a){m||||} x ::download_conversation_task[b](https://e.test/b) y ::artifact[c]'
    expect(stripModelAuthoredMarkers(text)).to.equal(' x  y ')
  })

  it('leaves ordinary markdown, links and citations untouched', () => {
    const text = 'See [the doc](https://docs.example.com/a) and [source](ref1). Use `::artifact` literally? no: ::not-a-marker[x]'
    expect(stripModelAuthoredMarkers(text)).to.equal(text)
  })

  it('handles empty input', () => {
    expect(stripModelAuthoredMarkers('')).to.equal('')
  })
})
