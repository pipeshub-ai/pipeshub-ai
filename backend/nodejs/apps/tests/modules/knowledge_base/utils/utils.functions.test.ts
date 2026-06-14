import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import nock from 'nock'
import {
  createPlaceholderDocument,
  uploadNextVersionToStorage,
  saveFileToStorageAndGetDocumentId,
} from '../../../../src/modules/knowledge_base/utils/utils'

const STORAGE_URL = 'http://localhost:19191'

function makeKVS(endpoint: string | null = STORAGE_URL) {
  // When endpoint is null, return a storage config with no endpoint so defaultConfig is used
  const url = endpoint !== null
    ? JSON.stringify({ storage: { endpoint } })
    : JSON.stringify({ storage: {} })
  return {
    get: sinon.stub().resolves(url),
  }
}

function makeReq(userId = 'user-1', authorization = 'Bearer test-token') {
  return {
    user: { userId },
    headers: { authorization },
  } as any
}

function makeFile(filename = 'test.pdf', mimetype = 'application/pdf') {
  return {
    buffer: Buffer.from('file content'),
    originalname: filename,
    mimetype,
    size: 12,
    fieldname: 'file',
  } as any
}

const defaultStorageConfig = { storageType: 'local', endpoint: STORAGE_URL }

describe('knowledge_base/utils - functional tests', () => {
  afterEach(() => {
    nock.cleanAll()
    sinon.restore()
  })

  // ===========================================================================
  // createPlaceholderDocument
  // ===========================================================================
  describe('createPlaceholderDocument', () => {
    it('should return documentId on direct upload success (200)', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(200, { _id: 'doc-001', documentName: 'test' })

      const result = await createPlaceholderDocument(
        makeReq(),
        makeFile('test.pdf'),
        'test.pdf',
        false,
        makeKVS() as any,
        defaultStorageConfig,
      )

      expect(result.documentId).to.equal('doc-001')
      expect(result.documentName).to.equal('test')
      expect(result.uploadPromise).to.be.undefined
      expect(result.redirectUrl).to.be.undefined
    })

    it('should fall back to defaultConfig.endpoint when KVS has no stored endpoint', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(200, { _id: 'doc-fallback', documentName: 'fallback' })

      const result = await createPlaceholderDocument(
        makeReq(),
        makeFile(),
        'test.pdf',
        true,
        makeKVS(null) as any,
        defaultStorageConfig,
      )

      expect(result.documentId).to.equal('doc-fallback')
    })

    it('should handle 201 response as success', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(201, { _id: 'doc-201', documentName: 'created' })

      const result = await createPlaceholderDocument(
        makeReq(),
        makeFile(),
        'test.pdf',
        false,
        makeKVS() as any,
        defaultStorageConfig,
      )

      expect(result.documentId).to.equal('doc-201')
    })

    it('should return placeholder with uploadPromise on 301 redirect', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(
          301,
          {},
          {
            location: 'https://s3.test/put?sig=abc',
            'x-document-id': 'doc-redirect-001',
            'x-document-name': 'redirect.pdf',
          },
        )

      // Intercept the S3 PUT triggered by uploadFileToSignedUrl
      nock('https://s3.test').put('/put').query({ sig: 'abc' }).reply(200)

      const result = await createPlaceholderDocument(
        makeReq(),
        makeFile('redirect.pdf'),
        'redirect.pdf',
        false,
        makeKVS() as any,
        defaultStorageConfig,
      )

      expect(result.documentId).to.equal('doc-redirect-001')
      expect(result.documentName).to.equal('redirect.pdf')
      expect(result.redirectUrl).to.equal('https://s3.test/put?sig=abc')
      expect(result.uploadPromise).to.be.instanceOf(Promise)
    })

    it('should throw on non-redirect error from storage', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(500, { error: 'Internal Server Error' })

      try {
        await createPlaceholderDocument(
          makeReq(),
          makeFile(),
          'test.pdf',
          false,
          makeKVS() as any,
          defaultStorageConfig,
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.exist
        expect(error.response?.status).to.equal(500)
      }
    })

    it('should throw on 404 error from storage', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(404, { error: 'Not found' })

      try {
        await createPlaceholderDocument(
          makeReq(),
          makeFile(),
          'test.pdf',
          false,
          makeKVS() as any,
          defaultStorageConfig,
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.exist
      }
    })

    it('should strip extension from documentName (getFilenameWithoutExtension)', async () => {
      let capturedPath = ''
      nock(STORAGE_URL)
        .post('/api/v1/document/upload', (body: any) => {
          // nock captures body — we can't easily inspect FormData here
          return true
        })
        .reply(200, { _id: 'doc-ext', documentName: 'report' })

      const result = await createPlaceholderDocument(
        makeReq(),
        makeFile('report.docx', 'application/vnd.openxmlformats'),
        'report.docx',
        false,
        makeKVS() as any,
        defaultStorageConfig,
      )

      expect(result.documentId).to.equal('doc-ext')
    })

    it('should forward Authorization header to storage', async () => {
      let capturedHeaders: any = {}
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(function (uri, body) {
          capturedHeaders = this.req.headers
          return [200, { _id: 'doc-auth', documentName: 'auth-test' }]
        })

      await createPlaceholderDocument(
        makeReq('user-2', 'Bearer special-token'),
        makeFile(),
        'auth-test.pdf',
        false,
        makeKVS() as any,
        defaultStorageConfig,
      )

      expect(capturedHeaders.authorization).to.equal('Bearer special-token')
    })
  })

  // ===========================================================================
  // uploadNextVersionToStorage
  // ===========================================================================
  describe('uploadNextVersionToStorage', () => {
    it('should return documentId and documentName on success', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/doc-123/uploadNextVersion')
        .reply(200, { _id: 'doc-123-v2', documentName: 'report-v2.pdf' })

      const result = await uploadNextVersionToStorage(
        makeReq(),
        makeFile('report.pdf'),
        'doc-123',
        makeKVS() as any,
        defaultStorageConfig,
      )

      expect(result.documentId).to.equal('doc-123-v2')
      expect(result.documentName).to.equal('report-v2.pdf')
    })

    it('should fall back to defaultConfig.endpoint when KVS has no stored endpoint', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/doc-456/uploadNextVersion')
        .reply(200, { _id: 'doc-456-v2', documentName: 'next.pdf' })

      const result = await uploadNextVersionToStorage(
        makeReq(),
        makeFile(),
        'doc-456',
        makeKVS(null) as any,
        defaultStorageConfig,
      )

      expect(result.documentId).to.equal('doc-456-v2')
    })

    it('should throw and log on storage error', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/bad-doc/uploadNextVersion')
        .reply(500, { message: 'Storage error' })

      try {
        await uploadNextVersionToStorage(
          makeReq(),
          makeFile(),
          'bad-doc',
          makeKVS() as any,
          defaultStorageConfig,
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.exist
        expect(error.response?.status).to.equal(500)
      }
    })

    it('should forward Authorization header', async () => {
      let capturedHeaders: any = {}
      nock(STORAGE_URL)
        .post('/api/v1/document/doc-777/uploadNextVersion')
        .reply(function (uri, body) {
          capturedHeaders = this.req.headers
          return [200, { _id: 'doc-777-v2', documentName: 'v2.pdf' }]
        })

      await uploadNextVersionToStorage(
        makeReq('user-3', 'Bearer my-token'),
        makeFile(),
        'doc-777',
        makeKVS() as any,
        defaultStorageConfig,
      )

      expect(capturedHeaders.authorization).to.equal('Bearer my-token')
    })
  })

  // ===========================================================================
  // saveFileToStorageAndGetDocumentId
  // ===========================================================================
  describe('saveFileToStorageAndGetDocumentId', () => {
    it('should return documentId and documentName on direct upload', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(200, { _id: 'save-doc-001', documentName: 'saved' })

      const result = await saveFileToStorageAndGetDocumentId(
        makeReq(),
        makeFile('saved.pdf'),
        'saved.pdf',
        false,
        {} as any, // _record (ignored)
        {} as any, // _fileRecord (ignored)
        makeKVS() as any,
        defaultStorageConfig,
        {} as any, // _recordRelationService (ignored)
      )

      expect(result.documentId).to.equal('save-doc-001')
      expect(result.documentName).to.equal('saved')
    })

    it('should await upload promise on redirect path', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(
          301,
          {},
          {
            location: 'https://s3.test/save-put',
            'x-document-id': 'save-redirect-doc',
            'x-document-name': 'save-redirect.pdf',
          },
        )

      // Intercept S3 PUT
      nock('https://s3.test').put('/save-put').reply(200)

      const result = await saveFileToStorageAndGetDocumentId(
        makeReq(),
        makeFile('save-redirect.pdf'),
        'save-redirect.pdf',
        true,
        {} as any,
        {} as any,
        makeKVS() as any,
        defaultStorageConfig,
        {} as any,
      )

      expect(result.documentId).to.equal('save-redirect-doc')
      expect(result.documentName).to.equal('save-redirect.pdf')
    })

    it('should throw when storage returns 500', async () => {
      nock(STORAGE_URL)
        .post('/api/v1/document/upload')
        .reply(500, { error: 'Server error' })

      try {
        await saveFileToStorageAndGetDocumentId(
          makeReq(),
          makeFile(),
          'error.pdf',
          false,
          {} as any,
          {} as any,
          makeKVS() as any,
          defaultStorageConfig,
          {} as any,
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.exist
      }
    })
  })
})
