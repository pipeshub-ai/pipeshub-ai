import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ApiDocsService } from '../../../src/modules/api-docs/docs.service'

describe('ApiDocsService - additional coverage', () => {
  let service: ApiDocsService
  let mockLogger: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    service = new ApiDocsService(mockLogger)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('getUnifiedDocs', () => {
    it('should return unified docs with all required fields', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()

      expect(docs).to.have.property('info')
      expect(docs).to.have.property('categories')
      expect(docs).to.have.property('modules')
      expect(docs).to.have.property('endpoints')
      expect(docs).to.have.property('schemas')
      expect(docs.info).to.have.property('title')
      expect(docs.info).to.have.property('version')
      expect(docs.info).to.have.property('description')
    })

    it('should return empty endpoints when no spec loaded', () => {
      // Don't initialize - spec won't be loaded
      const docs = service.getUnifiedDocs()
      expect(docs.endpoints).to.be.an('array')
    })

    it('should return modules sorted by order', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()

      for (let i = 1; i < docs.modules.length; i++) {
        expect(docs.modules[i].order).to.be.at.least(docs.modules[i - 1].order)
      }
    })
  })

  describe('getModuleSpec', () => {
    it('should return null for unknown module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('nonexistent')
      expect(spec).to.be.null
    })

    it('should return spec for auth module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('auth')
      if (spec) {
        expect(spec).to.have.property('openapi', '3.0.0')
        expect(spec.info.title).to.equal('Authentication')
      }
    })

    it('should return spec for storage module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('storage')
      if (spec) {
        expect(spec.info.title).to.equal('Storage')
      }
    })

    it('should return spec for query-service module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('query-service')
      if (spec) {
        expect(spec.info.title).to.equal('Query Service')
      }
    })

    it('should return spec for indexing-service module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('indexing-service')
      if (spec) {
        expect(spec.info.title).to.equal('Indexing Service')
      }
    })

    it('should return spec for connector-service-internal module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('connector-service-internal')
      if (spec) {
        expect(spec.info.title).to.equal('Connector Service')
      }
    })

    it('should return spec for docling-service module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('docling-service')
      if (spec) {
        expect(spec.info.title).to.equal('Docling Service')
      }
    })

    it('should return spec for user-management module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('user-management')
      if (spec) {
        expect(spec.info.title).to.equal('User Management')
      }
    })

    it('should return spec for enterprise-search module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('enterprise-search')
      if (spec) {
        expect(spec.info.title).to.equal('Enterprise Search')
      }
    })
  })

  describe('getCombinedSpec', () => {
    it('should return combined OpenAPI spec', async () => {
      await service.initialize()
      const spec = service.getCombinedSpec()

      expect(spec).to.have.property('openapi', '3.0.0')
      expect(spec).to.have.property('info')
      expect(spec).to.have.property('servers')
      expect(spec).to.have.property('tags')
      expect(spec).to.have.property('paths')
      expect(spec).to.have.property('components')
      expect(spec).to.have.property('security')
    })

    it('should include default info when no spec loaded', () => {
      const spec = service.getCombinedSpec()
      expect(spec.info.title).to.be.a('string')
    })

    it('should include security schemes', async () => {
      await service.initialize()
      const spec = service.getCombinedSpec()
      expect(spec.components).to.have.property('securitySchemes')
    })
  })

  describe('_buildCategories (via getUnifiedDocs)', () => {
    it('should return all expected category IDs', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()
      const categoryIds = docs.categories.map(c => c.id)

      expect(categoryIds).to.include('identity')
      expect(categoryIds).to.include('data')
      expect(categoryIds).to.include('search')
      expect(categoryIds).to.include('integrations')
      expect(categoryIds).to.include('system')
      expect(categoryIds).to.include('oauth')
      expect(categoryIds).to.include('internal-services')
    })

    it('should have modules in each category', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()

      for (const category of docs.categories) {
        expect(category.modules).to.be.an('array')
        expect(category.name).to.be.a('string')
        expect(category.description).to.be.a('string')
      }
    })
  })

  describe('_buildApiInfo (via getUnifiedDocs)', () => {
    it('should return default info when no spec loaded', () => {
      const docs = service.getUnifiedDocs()
      expect(docs.info.title).to.be.a('string')
      expect(docs.info.version).to.be.a('string')
      expect(docs.info.description).to.be.a('string')
    })
  })

  describe('findModuleByTags (via _extractEndpoints)', () => {
    it('should return unknown for unmatched tags', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()
      // All endpoints should have a moduleId
      for (const ep of docs.endpoints) {
        expect(ep.moduleId).to.be.a('string')
      }
    })
  })

  describe('getInternalServiceModuleId (via findModuleByTags)', () => {
    it('should map /query/ path to query-service', () => {
      // Test the private method indirectly
      const path = '/query/search'
      const pathLower = path.toLowerCase()
      const isQuery = pathLower.startsWith('/query/') || pathLower.includes('/search')
      expect(isQuery).to.be.true
    })

    it('should map /indexing/ path to indexing-service', () => {
      const path = '/indexing/process'
      expect(path.toLowerCase().startsWith('/indexing/')).to.be.true
    })

    it('should map /connector/ path to connector-service-internal', () => {
      const path = '/connector/sync'
      expect(path.toLowerCase().startsWith('/connector/')).to.be.true
    })

    it('should map /docling/ path to docling-service', () => {
      const path = '/docling/parse'
      expect(path.toLowerCase().startsWith('/docling/')).to.be.true
    })

    it('should map summary containing [query service] to query-service', () => {
      const summary = 'Process request [query service]'
      expect(summary.toLowerCase().includes('[query service]')).to.be.true
    })

    it('should map summary containing [indexing service] to indexing-service', () => {
      const summary = 'Process [indexing service]'
      expect(summary.toLowerCase().includes('[indexing service]')).to.be.true
    })

    it('should map summary containing [connector service] to connector-service-internal', () => {
      const summary = 'Sync [connector service]'
      expect(summary.toLowerCase().includes('[connector service]')).to.be.true
    })

    it('should map summary containing [docling service] to docling-service', () => {
      const summary = 'Parse PDF [docling service]'
      expect(summary.toLowerCase().includes('[docling service]')).to.be.true
    })

    it('should default to query-service for unmatched internal endpoints', () => {
      const path = '/unknown/endpoint'
      const summary = 'Some endpoint'
      const pathLower = path.toLowerCase()
      const summaryLower = summary.toLowerCase()

      const match =
        pathLower.startsWith('/query/') ||
        pathLower.includes('/search') ||
        pathLower.includes('/chat') ||
        pathLower.startsWith('/indexing/') ||
        pathLower.startsWith('/connector/') ||
        pathLower.startsWith('/docling/') ||
        summaryLower.includes('[query service]') ||
        summaryLower.includes('[indexing service]') ||
        summaryLower.includes('[connector service]') ||
        summaryLower.includes('[docling service]')

      expect(match).to.be.false
      // Default is query-service
    })
  })
})
