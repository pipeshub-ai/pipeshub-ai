import 'reflect-metadata'
import { expect } from 'chai'
import { buildSearchResponseForClient } from '../../../../src/modules/enterprise_search/controller/es_controller'

describe('POST /search response for the client', () => {
  it('keeps the flag that says the store answered with less than its full search', () => {
    const out = buildSearchResponseForClient({ searchResults: [], records: [], searchDegraded: true } as never)
    expect(out.searchDegraded).to.equal(true)
  })

  it('adds none when the search was complete', () => {
    const out = buildSearchResponseForClient({ searchResults: [], records: [] } as never)
    expect(out).to.not.have.property('searchDegraded')
  })
})
