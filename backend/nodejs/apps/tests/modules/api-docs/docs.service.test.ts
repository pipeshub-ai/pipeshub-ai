import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ApiDocsService } from '../../../src/modules/api-docs/docs.service'

describe('api-docs/docs.service', () => {
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

  describe('constructor', () => {
    it('should create instance', () => {
      expect(service).to.be.instanceOf(ApiDocsService)
    })
  })

  describe('initialize', () => {
    it('should not throw on initialization', async () => {
      await service.initialize()
      // Should not throw even if spec file is not found
    })

    it('should be idempotent (second call is no-op)', async () => {
      await service.initialize()
      await service.initialize()
      // Second call should not re-initialize
    })
  })

  describe('getModules', () => {
    it('should list the nav items of the spec in sidebar order', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()
      expect(service.getModules()).to.deep.equal(docs.categories.flatMap(c => c.modules))
    })

    it('should include expected module IDs', async () => {
      await service.initialize()
      const modules = service.getModules()
      const ids = modules.map(m => m.id)
      expect(ids).to.include('authentication')
      expect(ids).to.include('storage')
      expect(ids).to.include('knowledge-base')
      expect(ids).to.include('enterprise-search')
      expect(ids).to.include('connector-manager')
      expect(ids).to.include('configuration-manager')
    })
  })

  describe('getUnifiedDocs', () => {
    it('should return unified docs structure', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()
      expect(docs).to.have.property('info')
      expect(docs).to.have.property('categories')
      expect(docs).to.have.property('modules')
      expect(docs).to.have.property('endpoints')
      expect(docs).to.have.property('schemas')
    })

    it('should have info with title and version', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()
      expect(docs.info).to.have.property('title')
      expect(docs.info).to.have.property('version')
      expect(docs.info).to.have.property('description')
    })

    it('should return the headings of x-pipeshub-nav in order', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()
      const names = docs.categories.map(c => c.name)
      expect(names.slice(0, 5)).to.deep.equal([
        'Identity & Access',
        'Data Management',
        'Search & AI',
        'Integrations',
        'System',
      ])
    })
  })

  describe('getModuleSpec', () => {
    it('should return null for non-existent module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('nonexistent')
      expect(spec).to.be.null
    })

    it('should return spec for valid module', async () => {
      await service.initialize()
      const spec = service.getModuleSpec('authentication')
      expect(spec).to.not.be.null
      expect(spec).to.have.property('openapi', '3.0.0')
      expect(spec!.info).to.have.property('title', 'Authentication')
      expect(Object.keys(spec!.paths)).to.not.be.empty
    })
  })

  describe('getCombinedSpec', () => {
    it('should return combined OpenAPI spec', async () => {
      await service.initialize()
      const spec = service.getCombinedSpec()
      expect(spec).to.have.property('openapi', '3.0.0')
      expect(spec).to.have.property('info')
      expect(spec).to.have.property('paths')
      expect(spec).to.have.property('components')
    })

  })
})
