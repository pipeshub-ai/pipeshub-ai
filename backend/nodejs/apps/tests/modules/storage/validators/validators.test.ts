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
  RollBackToPreviousVersionSchema,
  DocumentIdParamsWithVersion,
} from '../../../../src/modules/storage/validators/validators'

const DOCUMENT_ID = '64d000000000000000000b01'

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
        params: { documentId: DOCUMENT_ID },
        headers: { authorization: 'Bearer token' },
        body: { fileBuffer: {} },
      }
      const result = DocumentIdParams.safeParse(data)
      expect(result.success).to.be.true
    })

    // A malformed id used to reach Mongoose, which failed to cast it and answered 500.
    for (const documentId of ['abc123', 'not-an-object-id', '64d000000000000000000b0z', '']) {
      it(`should reject the malformed documentId ${JSON.stringify(documentId)} with a message that says what to send`, () => {
        const result = DocumentIdParams.safeParse({
          params: { documentId },
          headers: { authorization: 'Bearer token' },
          body: { fileBuffer: {} },
        })
        expect(result.success).to.be.false
        if (result.success) return
        expect(result.error.issues[0]?.path).to.deep.equal(['params', 'documentId'])
        expect(result.error.issues[0]?.message).to.contain('24-character id')
      })
    }

    it('should reject a malformed documentId on every schema that takes one', () => {
      const params = { documentId: 'abc123' }
      const headers = { authorization: 'Bearer token' }
      expect(GetBufferSchema.safeParse({ body: {}, query: {}, params, headers }).success).to.be.false
      expect(RollBackToPreviousVersionSchema.safeParse({ body: { note: 'n' }, query: {}, params, headers }).success).to.be.false
      expect(DirectUploadSchema.safeParse({ query: {}, params, headers }).success).to.be.false
      expect(UploadNextVersionSchema.safeParse({ params, body: { fileBuffer: {} } }).success).to.be.false
      expect(DocumentIdParamsWithVersion.safeParse({ params, headers, query: {} }).success).to.be.false
    })
  })

  describe('GetBufferSchema', () => {
    it('should accept valid request with optional version', () => {
      const data = {
        body: {},
        query: { version: '2' },
        params: { documentId: DOCUMENT_ID },
        headers: { authorization: 'Bearer token' },
      }
      const result = GetBufferSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept request without version', () => {
      const data = {
        body: {},
        query: {},
        params: { documentId: DOCUMENT_ID },
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

    it('should accept valid customMetadata array', () => {
      const data = {
        body: {
          documentName: 'test',
          documentPath: '/path/to/doc',
          extension: 'pdf',
          customMetadata: [{ key: 'source', value: 'api' }],
        },
        query: {},
        params: {},
        headers: { authorization: 'Bearer token' },
      }
      const result = CreateDocumentSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid customMetadata shape', () => {
      const data = {
        body: {
          documentName: 'test',
          documentPath: '/path/to/doc',
          extension: 'pdf',
          customMetadata: [{ value: 'api' }],
        },
        query: {},
        params: {},
        headers: { authorization: 'Bearer token' },
      }
      const result = CreateDocumentSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('RollBackToPreviousVersionSchema', () => {
    const baseData = {
      body: { note: 'rollback' },
      query: {},
      params: { documentId: DOCUMENT_ID },
      headers: { authorization: 'Bearer token' },
    }

    it('should accept request without body.version', () => {
      const result = RollBackToPreviousVersionSchema.safeParse(baseData)
      expect(result.success).to.be.true
    })

    it('should accept numeric body.version', () => {
      const data = {
        ...baseData,
        body: { ...baseData.body, version: 2 },
      }
      const result = RollBackToPreviousVersionSchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.version).to.equal(2)
      }
    })

    it('should reject string body.version (strict number required)', () => {
      const data = {
        ...baseData,
        body: { ...baseData.body, version: '2' },
      }
      const result = RollBackToPreviousVersionSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject non-integer body.version', () => {
      const data = {
        ...baseData,
        body: { ...baseData.body, version: 1.5 },
      }
      const result = RollBackToPreviousVersionSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject negative body.version', () => {
      const data = {
        ...baseData,
        body: { ...baseData.body, version: -1 },
      }
      const result = RollBackToPreviousVersionSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept version 0 in body', () => {
      const data = {
        ...baseData,
        body: { ...baseData.body, version: 0 },
      }
      const result = RollBackToPreviousVersionSchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.version).to.equal(0)
      }
    })
  })

  // The `version` query param on DocumentIdParamsWithVersion / GetBufferSchema
  // now allows 0 (previously required > 0). See validators.ts review fix.
  describe('DocumentIdParamsWithVersion - query.version', () => {
    // Import here to avoid a top-level cycle if other tests don't need it
    const {
      DocumentIdParamsWithVersion,
    } = require('../../../../src/modules/storage/validators/validators')

    it('should accept version "0"', () => {
      const data = {
        query: { version: '0' },
        params: { documentId: DOCUMENT_ID },
        headers: { authorization: 'Bearer token' },
      }
      const result = DocumentIdParamsWithVersion.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.version).to.equal(0)
      }
    })

    it('should reject negative version "-1"', () => {
      const data = {
        query: { version: '-1' },
        params: { documentId: DOCUMENT_ID },
        headers: { authorization: 'Bearer token' },
      }
      const result = DocumentIdParamsWithVersion.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('DirectUploadSchema', () => {
    it('should accept valid direct upload schema', () => {
      const data = {
        query: {},
        params: { documentId: DOCUMENT_ID },
        headers: { authorization: 'Bearer token' },
      }
      const result = DirectUploadSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })
})
