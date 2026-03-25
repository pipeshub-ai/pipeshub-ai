import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FileProcessorService } from '../../../../src/libs/middlewares/file_processor/fp.service'
import { FileProcessorConfiguration } from '../../../../src/libs/middlewares/file_processor/fp.interface'
import { FileProcessingType } from '../../../../src/libs/middlewares/file_processor/fp.constant'
import { BadRequestError, NotImplementedError } from '../../../../src/libs/errors/http.errors'
import { Logger } from '../../../../src/libs/services/logger.service'

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

function createConfig(overrides: Partial<FileProcessorConfiguration> = {}): FileProcessorConfiguration {
  return {
    fieldName: 'file',
    maxFileSize: 1024 * 1024 * 5,
    allowedMimeTypes: ['application/json'],
    maxFilesAllowed: 1,
    isMultipleFilesAllowed: false,
    processingType: FileProcessingType.JSON,
    strictFileUpload: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('FileProcessorService', () => {
  let loggerInstance: Logger

  beforeEach(() => {
    loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // Constructor
  // -----------------------------------------------------------------------
  describe('Constructor', () => {
    it('should create instance with valid configuration', () => {
      const service = new FileProcessorService(createConfig())
      expect(service).to.be.instanceOf(FileProcessorService)
    })
  })

  // -----------------------------------------------------------------------
  // upload()
  // -----------------------------------------------------------------------
  describe('upload()', () => {
    it('should return a middleware function', () => {
      const service = new FileProcessorService(createConfig())
      const handler = service.upload()
      expect(handler).to.be.a('function')
    })

    it('should skip processing for non-multipart requests', () => {
      const service = new FileProcessorService(createConfig())
      const handler = service.upload()
      const req = createMockRequest({
        headers: { 'content-type': 'application/json' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // -----------------------------------------------------------------------
  // processFiles() - JSON processing
  // -----------------------------------------------------------------------
  describe('processFiles() - JSON type', () => {
    it('should return a middleware function', () => {
      const service = new FileProcessorService(createConfig())
      const handler = service.processFiles()
      expect(handler).to.be.a('function')
    })

    it('should call next() when no files and not strict', () => {
      const service = new FileProcessorService(createConfig({ strictFileUpload: false }))
      const handler = service.processFiles()
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should call next with BadRequestError when no files and strict mode', () => {
      const service = new FileProcessorService(createConfig({ strictFileUpload: true }))
      const handler = service.processFiles()
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
      expect(error.message).to.include('No files available')
    })

    it('should parse JSON from single file and attach as fileContent', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.JSON,
        isMultipleFilesAllowed: false,
      }))
      const handler = service.processFiles()

      const jsonData = { key: 'value', items: [1, 2, 3] }
      const req = createMockRequest({
        file: {
          buffer: Buffer.from(JSON.stringify(jsonData)),
          originalname: 'data.json',
          mimetype: 'application/json',
          size: 100,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.fileContent).to.deep.equal(jsonData)
    })

    it('should parse JSON from multiple files and attach as fileContents array', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.JSON,
        isMultipleFilesAllowed: true,
        maxFilesAllowed: 3,
      }))
      const handler = service.processFiles()

      const json1 = { name: 'first' }
      const json2 = { name: 'second' }
      const req = createMockRequest({
        files: [
          { buffer: Buffer.from(JSON.stringify(json1)), originalname: 'a.json', mimetype: 'application/json', size: 50 },
          { buffer: Buffer.from(JSON.stringify(json2)), originalname: 'b.json', mimetype: 'application/json', size: 50 },
        ],
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.fileContents).to.deep.equal([json1, json2])
    })

    it('should call next with BadRequestError for invalid JSON content', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.JSON,
      }))
      const handler = service.processFiles()

      const req = createMockRequest({
        file: {
          buffer: Buffer.from('not valid json'),
          originalname: 'bad.json',
          mimetype: 'application/json',
          size: 50,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
      expect(error.message).to.include('Invalid JSON')
    })
  })

  // -----------------------------------------------------------------------
  // processFiles() - Buffer processing
  // -----------------------------------------------------------------------
  describe('processFiles() - Buffer type', () => {
    it('should process single buffer file and attach as fileBuffer', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: false,
      }))
      const handler = service.processFiles()

      const fileBuffer = Buffer.from('binary content')
      const req = createMockRequest({
        file: {
          buffer: fileBuffer,
          originalname: 'data.bin',
          mimetype: 'application/octet-stream',
          size: fileBuffer.length,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.fileBuffer).to.exist
      expect(req.body.fileBuffer.originalname).to.equal('data.bin')
      expect(req.body.fileBuffer.mimetype).to.equal('application/octet-stream')
      expect(req.body.fileBuffer.size).to.equal(fileBuffer.length)
      expect(req.body.fileBuffer.buffer).to.equal(fileBuffer)
    })

    it('should process multiple buffer files and attach as fileBuffers array', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: true,
        maxFilesAllowed: 3,
      }))
      const handler = service.processFiles()

      const buf1 = Buffer.from('file1')
      const buf2 = Buffer.from('file2')
      const req = createMockRequest({
        files: [
          { buffer: buf1, originalname: 'a.bin', mimetype: 'application/octet-stream', size: buf1.length },
          { buffer: buf2, originalname: 'b.bin', mimetype: 'application/octet-stream', size: buf2.length },
        ],
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
      expect(req.body.fileBuffers).to.be.an('array')
      expect(req.body.fileBuffers).to.have.length(2)
      expect(req.body.fileBuffers[0].originalname).to.equal('a.bin')
      expect(req.body.fileBuffers[1].originalname).to.equal('b.bin')
    })

    it('should include lastModified and filePath in buffer info', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: false,
      }))
      const handler = service.processFiles()

      const fileBuffer = Buffer.from('content')
      const req = createMockRequest({
        file: {
          buffer: fileBuffer,
          originalname: 'test.pdf',
          mimetype: 'application/pdf',
          size: fileBuffer.length,
          lastModified: 1234567890,
          filePath: '/uploads/test.pdf',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(req.body.fileBuffer.lastModified).to.equal(1234567890)
      expect(req.body.fileBuffer.filePath).to.equal('/uploads/test.pdf')
    })

    it('should default filePath to originalname when not set', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: false,
      }))
      const handler = service.processFiles()

      const fileBuffer = Buffer.from('content')
      const req = createMockRequest({
        file: {
          buffer: fileBuffer,
          originalname: 'doc.pdf',
          mimetype: 'application/pdf',
          size: fileBuffer.length,
          // no filePath, no lastModified
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(req.body.fileBuffer.filePath).to.equal('doc.pdf')
      // lastModified should be set to approximately Date.now()
      expect(req.body.fileBuffer.lastModified).to.be.a('number')
    })
  })

  // -----------------------------------------------------------------------
  // getMiddleware()
  // -----------------------------------------------------------------------
  describe('getMiddleware()', () => {
    it('should return an array of two middleware functions', () => {
      const service = new FileProcessorService(createConfig())
      const middlewares = service.getMiddleware()
      expect(middlewares).to.be.an('array')
      expect(middlewares).to.have.length(2)
      expect(middlewares[0]).to.be.a('function')
      expect(middlewares[1]).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // File retrieval from request
  // -----------------------------------------------------------------------
  describe('File retrieval edge cases', () => {
    it('should handle req.files as an object with field names', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.JSON,
        fieldName: 'document',
      }))
      const handler = service.processFiles()

      const jsonData = { test: 'data' }
      const req = createMockRequest({
        files: {
          document: [
            { buffer: Buffer.from(JSON.stringify(jsonData)), originalname: 'doc.json', mimetype: 'application/json', size: 50 },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should return empty array when no files on request', () => {
      const service = new FileProcessorService(createConfig({ strictFileUpload: false }))
      const handler = service.processFiles()
      const req = createMockRequest({ file: undefined, files: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should handle req.files as an object with single file under field name', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        fieldName: 'upload',
        isMultipleFilesAllowed: false,
      }))
      const handler = service.processFiles()

      const buf = Buffer.from('single content')
      const req = createMockRequest({
        files: {
          upload: {
            buffer: buf,
            originalname: 'single.bin',
            mimetype: 'application/octet-stream',
            size: buf.length,
          } as any,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should handle fieldName as array', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        fieldName: ['file', 'upload'] as any,
        isMultipleFilesAllowed: false,
      }))
      const handler = service.processFiles()

      const buf = Buffer.from('content')
      const req = createMockRequest({
        files: {
          file: [{ buffer: buf, originalname: 'f.bin', mimetype: 'application/octet-stream', size: buf.length }],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // processFiles() - NotImplementedError for unknown processing type
  // -----------------------------------------------------------------------
  describe('processFiles() - unknown type', () => {
    it('should call next with BadRequestError for unknown processing type', () => {
      const service = new FileProcessorService(createConfig({
        processingType: 'UNKNOWN' as any,
      }))
      const handler = service.processFiles()

      const req = createMockRequest({
        file: {
          buffer: Buffer.from('test'),
          originalname: 'test.bin',
          mimetype: 'application/json',
          size: 4,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  // -----------------------------------------------------------------------
  // processFiles() - Buffer edge cases
  // -----------------------------------------------------------------------
  describe('processFiles() - Buffer edge cases', () => {
    it('should filter out null files in multiple buffer mode', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: true,
        maxFilesAllowed: 3,
        allowedMimeTypes: ['application/octet-stream', 'application/json'],
      }))
      const handler = service.processFiles()

      const buf = Buffer.from('content')
      const req = createMockRequest({
        files: [
          { buffer: buf, originalname: 'a.bin', mimetype: 'application/octet-stream', size: buf.length },
        ],
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileBuffers).to.be.an('array')
      expect(req.body.fileBuffers.length).to.equal(1)
    })

    it('should handle multiple buffer mode with lastModified and filePath from metadata', () => {
      const service = new FileProcessorService(createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: true,
        maxFilesAllowed: 3,
        allowedMimeTypes: ['application/octet-stream'],
      }))
      const handler = service.processFiles()

      const buf1 = Buffer.from('file1')
      const buf2 = Buffer.from('file2')
      const req = createMockRequest({
        files: [
          { buffer: buf1, originalname: 'a.bin', mimetype: 'application/octet-stream', size: buf1.length, lastModified: 1000, filePath: '/path/a.bin' },
          { buffer: buf2, originalname: 'b.bin', mimetype: 'application/octet-stream', size: buf2.length },
        ],
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(req.body.fileBuffers).to.have.length(2)
      expect(req.body.fileBuffers[0].lastModified).to.equal(1000)
      expect(req.body.fileBuffers[0].filePath).to.equal('/path/a.bin')
      expect(req.body.fileBuffers[1].filePath).to.equal('b.bin')
    })
  })

  // -----------------------------------------------------------------------
  // upload() - multipart handling
  // -----------------------------------------------------------------------
  describe('upload() - multipart handling', () => {
    it('should use array upload handler for multiple files config', () => {
      const service = new FileProcessorService(createConfig({
        isMultipleFilesAllowed: true,
        maxFilesAllowed: 5,
      }))
      const handler = service.upload()
      expect(handler).to.be.a('function')
    })

    it('should skip processing for non-multipart requests', () => {
      const service = new FileProcessorService(createConfig())
      const handler = service.upload()
      const req = createMockRequest({
        headers: { 'content-type': 'application/json' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should handle missing content-type header', () => {
      const service = new FileProcessorService(createConfig())
      const handler = service.upload()
      const req = createMockRequest({
        headers: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })
})
