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
    it('should return sorted modules after initialization', async () => {
      await service.initialize()
      const modules = service.getModules()
      expect(modules).to.be.an('array')
      // Verify modules are sorted by order
      for (let i = 1; i < modules.length; i++) {
        expect(modules[i].order).to.be.at.least(modules[i - 1].order)
      }
    })

    it('should include expected module IDs', async () => {
      await service.initialize()
      const modules = service.getModules()
      const ids = modules.map(m => m.id)
      expect(ids).to.include('auth')
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

    it('should return expected categories', async () => {
      await service.initialize()
      const docs = service.getUnifiedDocs()
      const categoryIds = docs.categories.map(c => c.id)
      expect(categoryIds).to.include('identity')
      expect(categoryIds).to.include('data')
      expect(categoryIds).to.include('search')
      expect(categoryIds).to.include('integrations')
      expect(categoryIds).to.include('system')
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
      const spec = service.getModuleSpec('auth')
      if (spec) {
        expect(spec).to.have.property('openapi', '3.0.0')
        expect(spec).to.have.property('info')
        expect(spec.info).to.have.property('title', 'Authentication')
      }
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

    it('should document chat attachment upload request and response contracts', async () => {
      await service.initialize()
      const spec = service.getCombinedSpec()

      const assistantUpload = spec.paths['/conversations/attachments/upload'].post
      const agentUpload = spec.paths['/agents/{agentKey}/conversations/attachments/upload'].post
      const agentDelete = spec.paths['/agents/{agentKey}/conversations/attachments/{recordId}'].delete
      const uploadResponse = spec.components.schemas.ChatAttachmentUploadResponse
      const uploadRef = spec.components.schemas.ChatAttachmentUploadRef

      expect(
        assistantUpload.requestBody.content['multipart/form-data'].schema.properties.conversationId.pattern,
      ).to.equal('^$|^[0-9a-fA-F]{24}$')
      expect(
        assistantUpload.requestBody.content['multipart/form-data'].schema.properties.files.minItems,
      ).to.equal(1)
      expect(
        assistantUpload.requestBody.content['multipart/form-data'].schema.properties.files.maxItems,
      ).to.equal(10)
      expect(assistantUpload.responses['200'].content['application/json'].schema.$ref).to.equal(
        '#/components/schemas/ChatAttachmentUploadResponse',
      )

      expect(agentUpload.parameters[0].schema.minLength).to.equal(1)
      expect(
        agentUpload.requestBody.content['multipart/form-data'].schema.properties.conversationId.pattern,
      ).to.equal('^$|^[0-9a-fA-F]{24}$')
      expect(agentUpload.responses['200'].content['application/json'].schema.$ref).to.equal(
        '#/components/schemas/ChatAttachmentUploadResponse',
      )
      expect(agentUpload.responses).to.have.property('500')
      expect(agentDelete.parameters[0].schema.minLength).to.equal(1)
      expect(agentDelete.parameters[1].schema.minLength).to.equal(1)
      expect(agentDelete.responses).to.have.property('403')
      expect(agentDelete.responses).to.have.property('default')
      expect(agentDelete.responses['204']).to.not.have.property('content')

      expect(uploadResponse.required).to.include('conversationId')
      expect(uploadResponse.required).to.include('attachments')
      expect(uploadResponse.properties.attachments.items.$ref).to.equal(
        '#/components/schemas/ChatAttachmentUploadRef',
      )

      expect(uploadRef.required).to.include('recordId')
      expect(uploadRef.required).to.include('recordName')
      expect(uploadRef.required).to.include('mimeType')
      expect(uploadRef.required).to.include('extension')
      expect(uploadRef.required).to.include('virtualRecordId')
      expect(uploadRef.properties).to.have.property('ocrMode')
    })
  })
})
