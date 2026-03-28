import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'

describe('enterprise_search/schema/search.schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  let EnterpriseSemanticSearch: mongoose.Model<any>

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('../../../../src/modules/enterprise_search/schema/search.schema')
    EnterpriseSemanticSearch = mod.default
  })

  it('should export the EnterpriseSemanticSearch model as default', () => {
    expect(EnterpriseSemanticSearch).to.exist
    expect(EnterpriseSemanticSearch.modelName).to.equal('search')
  })

  describe('schema paths', () => {
    it('should have query as required string', () => {
      const path = EnterpriseSemanticSearch.schema.path('query')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
      expect(path.instance).to.equal('String')
    })

    it('should have limit as required number', () => {
      const path = EnterpriseSemanticSearch.schema.path('limit')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
      expect(path.instance).to.equal('Number')
    })

    it('should have orgId as required', () => {
      const path = EnterpriseSemanticSearch.schema.path('orgId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have userId as required', () => {
      const path = EnterpriseSemanticSearch.schema.path('userId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have citationIds as array of ObjectIds', () => {
      const path = EnterpriseSemanticSearch.schema.path('citationIds')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })

    it('should have records as Map', () => {
      const path = EnterpriseSemanticSearch.schema.path('records')
      expect(path).to.exist
      expect(path.instance).to.equal('Map')
    })

    it('should have isShared with default false', () => {
      const path = EnterpriseSemanticSearch.schema.path('isShared')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(false)
    })

    it('should have isArchived with default false', () => {
      const path = EnterpriseSemanticSearch.schema.path('isArchived')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(false)
    })

    it('should have sharedWith with accessLevel enum', () => {
      const path = EnterpriseSemanticSearch.schema.path('sharedWith')
      expect(path).to.exist
    })
  })

  describe('timestamps', () => {
    it('should have timestamps enabled', () => {
      expect(EnterpriseSemanticSearch.schema.options.timestamps).to.equal(true)
    })
  })

  describe('indexes', () => {
    it('should have text index on query', () => {
      const indexes = EnterpriseSemanticSearch.schema.indexes()
      expect(indexes.length).to.be.greaterThan(0)
    })
  })
})
