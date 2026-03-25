import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  UploadNewSchema,
  DocumentIdParams,
  GetBufferSchema,
  CreateDocumentSchema,
  UploadNextVersionSchema,
  DirectUploadSchema,
} from '../../../../src/modules/storage/validators/validators'

describe('storage/validators/validators', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('UploadNewSchema', () => {
    it('should accept valid upload body', () => {
      const data = {
        body: {
          documentName: 'test-doc',
          isVersionedFile: 'true',
          fileBuffer: Buffer.from('hello'),
        },
        query: {},
        params: {},
        headers: { authorization: 'Bearer token123' },
      }
      const result = UploadNewSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject when both fileBuffer and fileBuffers are missing', () => {
      const data = {
        body: {
          documentName: 'test-doc',
          isVersionedFile: 'true',
        },
        query: {},
        params: {},
        headers: { authorization: 'Bearer token123' },
      }
      const result = UploadNewSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject when documentName is missing', () => {
      const data = {
        body: {
          isVersionedFile: 'true',
          fileBuffer: Buffer.from('hello'),
        },
        query: {},
        params: {},
        headers: { authorization: 'Bearer token123' },
      }
      const result = UploadNewSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('DocumentIdParams', () => {
    it('should accept valid documentId params', () => {
      const data = {
        params: { documentId: 'abc123' },
        headers: { authorization: 'Bearer token' },
        body: { fileBuffer: {} },
      }
      const result = DocumentIdParams.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('GetBufferSchema', () => {
    it('should accept valid request with optional version', () => {
      const data = {
        body: {},
        query: { version: '2' },
        params: { documentId: 'abc123' },
        headers: { authorization: 'Bearer token' },
      }
      const result = GetBufferSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept request without version', () => {
      const data = {
        body: {},
        query: {},
        params: { documentId: 'abc123' },
        headers: { authorization: 'Bearer token' },
      }
      const result = GetBufferSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('CreateDocumentSchema', () => {
    it('should accept valid document creation data', () => {
      const data = {
        body: {
          documentName: 'test',
          documentPath: '/path/to/doc',
          extension: 'pdf',
        },
        query: {},
        params: {},
        headers: { authorization: 'Bearer token' },
      }
      const result = CreateDocumentSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing documentName', () => {
      const data = {
        body: {
          documentPath: '/path/to/doc',
          extension: 'pdf',
        },
        query: {},
        params: {},
        headers: { authorization: 'Bearer token' },
      }
      const result = CreateDocumentSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('DirectUploadSchema', () => {
    it('should accept valid direct upload schema', () => {
      const data = {
        query: {},
        params: { documentId: 'abc123' },
        headers: { authorization: 'Bearer token' },
      }
      const result = DirectUploadSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })
})
