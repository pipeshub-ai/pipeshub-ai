import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FileUploadMiddleware } from '../../../../src/libs/middlewares/file_processor/fp.middleware'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: { 'content-type': 'multipart/form-data' },
    body: {},
    params: {},
    query: {},
    path: '/test',
    method: 'POST',
    ip: '127.0.0.1',
    get: sinon.stub(),
    file: undefined,
    files: undefined,
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.send.returns(res)
  res.setHeader.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('FileUploadMiddleware', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // Constructor
  // -----------------------------------------------------------------------
  describe('Constructor', () => {
    it('should create instance with default options merged with provided options', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/pdf'],
      })
      expect(middleware).to.be.instanceOf(FileUploadMiddleware)
    })

    it('should create instance with custom options', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['image/png', 'image/jpeg'],
        maxFileSize: 1024 * 1024 * 10,
        maxFiles: 5,
        fileFieldName: 'images',
        validateFileContent: true,
      })
      expect(middleware).to.be.instanceOf(FileUploadMiddleware)
    })
  })

  // -----------------------------------------------------------------------
  // singleFileUpload
  // -----------------------------------------------------------------------
  describe('singleFileUpload', () => {
    it('should return a middleware function', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.singleFileUpload()
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // multipleFileUpload
  // -----------------------------------------------------------------------
  describe('multipleFileUpload', () => {
    it('should return a middleware function', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
        maxFiles: 3,
      })
      const handler = middleware.multipleFileUpload()
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // validateJSONContent
  // -----------------------------------------------------------------------
  describe('validateJSONContent', () => {
    it('should return a middleware function', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.validateJSONContent()
      expect(handler).to.be.a('function')
    })

    it('should call next() when no files are present', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.validateJSONContent()
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should parse JSON from a single file and attach as fileConfig', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.validateJSONContent()

      const jsonContent = { key: 'value', nested: { a: 1 } }
      const req = createMockRequest({
        file: {
          buffer: Buffer.from(JSON.stringify(jsonContent)),
          originalname: 'config.json',
          mimetype: 'application/json',
          size: 100,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.fileConfig).to.deep.equal(jsonContent)
    })

    it('should parse JSON from multiple files and attach as fileConfigs array', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
        maxFiles: 3,
      })
      const handler = middleware.validateJSONContent()

      const json1 = { name: 'first' }
      const json2 = { name: 'second' }
      const req = createMockRequest({
        files: [
          {
            buffer: Buffer.from(JSON.stringify(json1)),
            originalname: 'config1.json',
          },
          {
            buffer: Buffer.from(JSON.stringify(json2)),
            originalname: 'config2.json',
          },
        ],
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.fileConfigs).to.be.an('array')
      expect(req.body.fileConfigs[0]).to.deep.equal(json1)
      expect(req.body.fileConfigs[1]).to.deep.equal(json2)
    })

    it('should call next with BadRequestError when file has invalid JSON', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.validateJSONContent()

      const req = createMockRequest({
        file: {
          buffer: Buffer.from('not valid json'),
          originalname: 'bad.json',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
      expect(error.message).to.equal('Invalid JSON format in uploaded file')
    })

    it('should call next() when files array is empty', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.validateJSONContent()

      const req = createMockRequest({
        files: [],
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next() when req.files is a non-array object (e.g., from fields upload)', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.validateJSONContent()

      // Multer .fields() returns an object, not an array
      const req = createMockRequest({
        files: { fieldName: [{ buffer: Buffer.from('test') }] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      // With non-array files and no req.file, files array is empty -> next()
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle validateJSONContent with req.file only (no req.files)', () => {
      const middleware = new FileUploadMiddleware({
        allowedMimeTypes: ['application/json'],
      })
      const handler = middleware.validateJSONContent()

      const req = createMockRequest({
        file: {
          buffer: Buffer.from(JSON.stringify({ config: true })),
          originalname: 'test.json',
        },
        files: undefined,
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.fileConfig).to.deep.equal({ config: true })
    })
  })
})
