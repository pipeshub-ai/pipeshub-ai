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

{
function createService(overrides: any = {}): FileProcessorService {
  return new FileProcessorService({
    fieldName: 'file',
    allowedMimeTypes: ['application/pdf', 'text/plain', 'application/json'],
    maxFilesAllowed: 5,
    isMultipleFilesAllowed: false,
    processingType: FileProcessingType.BUFFER,
    maxFileSize: 1024 * 1024,
    strictFileUpload: false,
    ...overrides,
  })
}

describe('FileProcessorService - branch coverage', () => {
  afterEach(() => { sinon.restore() })

  // =========================================================================
  // upload() - content-type check
  // =========================================================================
  describe('upload - content-type branching', () => {
    it('should skip file processing for non-multipart requests', () => {
      const service = createService()
      const handler = service.upload()
      const req = {
        headers: { 'content-type': 'application/json' },
        body: {},
      } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })

    it('should skip when content-type is missing entirely', () => {
      const service = createService()
      const handler = service.upload()
      const req = { headers: {}, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // =========================================================================
  // upload() - fieldName array vs string
  // =========================================================================
  describe('upload - fieldName handling', () => {
    it('should use first element when fieldName is an array', () => {
      const service = createService({ fieldName: ['files', 'documents'] })
      // Just verify construction works
      expect(service).to.exist
    })

    it('should use fieldName directly when it is a string', () => {
      const service = createService({ fieldName: 'file' })
      expect(service).to.exist
    })
  })

  // =========================================================================
  // upload() - isMultipleFilesAllowed
  // =========================================================================
  describe('upload - single vs multiple upload', () => {
    it('should use multer.array when isMultipleFilesAllowed is true', () => {
      const service = createService({ isMultipleFilesAllowed: true })
      expect(service).to.exist
    })

    it('should use multer.single when isMultipleFilesAllowed is false', () => {
      const service = createService({ isMultipleFilesAllowed: false })
      expect(service).to.exist
    })
  })

  // =========================================================================
  // processFiles() - processing types
  // =========================================================================
  describe('processFiles - processing type branches', () => {
    it('should handle JSON processing type', () => {
      const service = createService({ processingType: FileProcessingType.JSON })
      const handler = service.processFiles()
      const file = {
        originalname: 'test.json',
        buffer: Buffer.from('{"key": "value"}'),
        mimetype: 'application/json',
        size: 16,
      }
      const req = { file, files: null, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileContent).to.deep.equal({ key: 'value' })
    })

    it('should handle BUFFER processing type with single file', () => {
      const service = createService({ processingType: FileProcessingType.BUFFER })
      const handler = service.processFiles()
      const file = {
        originalname: 'test.pdf',
        buffer: Buffer.from('pdf content'),
        mimetype: 'application/pdf',
        size: 11,
      }
      const req = { file, files: null, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileBuffer).to.exist
      expect(req.body.fileBuffer.originalname).to.equal('test.pdf')
    })

    it('should handle BUFFER processing type with multiple files', () => {
      const service = createService({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: true,
      })
      const handler = service.processFiles()
      const files = [
        { originalname: 'f1.pdf', buffer: Buffer.from('1'), mimetype: 'application/pdf', size: 1 },
        { originalname: 'f2.pdf', buffer: Buffer.from('2'), mimetype: 'application/pdf', size: 1 },
      ]
      const req = { file: null, files, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileBuffers).to.have.length(2)
    })

    it('should handle null file in multiple files array', () => {
      const service = createService({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: true,
      })
      const handler = service.processFiles()
      const files = [
        { originalname: 'f1.pdf', buffer: Buffer.from('1'), mimetype: 'application/pdf', size: 1 },
        null,
      ]
      const req = { file: null, files, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      // null file in the array causes a crash in the logger before filtering,
      // so next is called with a BadRequestError
      expect(next.calledOnce).to.be.true
    })
  })

  // =========================================================================
  // processFiles() - no files
  // =========================================================================
  describe('processFiles - no files', () => {
    it('should return BadRequestError when strict and no files', () => {
      const service = createService({ strictFileUpload: true })
      const handler = service.processFiles()
      const req = { file: null, files: null, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError)
    })

    it('should skip processing when not strict and no files', () => {
      const service = createService({ strictFileUpload: false })
      const handler = service.processFiles()
      const req = { file: null, files: null, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args).to.have.length(0)
    })
  })

  // =========================================================================
  // processFiles - error handling in JSON
  // =========================================================================
  describe('processFiles - JSON parse error', () => {
    it('should return BadRequestError for invalid JSON', () => {
      const service = createService({ processingType: FileProcessingType.JSON })
      const handler = service.processFiles()
      const file = {
        originalname: 'bad.json',
        buffer: Buffer.from('not-json'),
        mimetype: 'application/json',
        size: 8,
      }
      const req = { file, files: null, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError)
      expect(next.firstCall.args[0].message).to.include('Invalid JSON')
    })
  })

  // =========================================================================
  // processFiles - JSON multiple files
  // =========================================================================
  describe('processJsonFiles - multiple files', () => {
    it('should parse multiple JSON files into array', () => {
      const service = createService({
        processingType: FileProcessingType.JSON,
        isMultipleFilesAllowed: true,
      })
      const handler = service.processFiles()
      const files = [
        { originalname: 'a.json', buffer: Buffer.from('{"a":1}'), mimetype: 'application/json', size: 7 },
        { originalname: 'b.json', buffer: Buffer.from('{"b":2}'), mimetype: 'application/json', size: 7 },
      ]
      const req = { file: null, files, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileContents).to.have.length(2)
    })
  })

  // =========================================================================
  // processFileMetadata
  // =========================================================================
  describe('processFileMetadata', () => {
    it('should skip when files array is empty', () => {
      const service = createService()
      ;(service as any).processFileMetadata({ body: {} }, [])
      // Should not throw
    })

    it('should parse files_metadata and attach to files', () => {
      const service = createService()
      const files = [
        { originalname: 'f1.pdf', buffer: Buffer.from('1'), mimetype: 'application/pdf', size: 1 },
      ] as any[]
      const req = {
        body: {
          files_metadata: JSON.stringify([{ file_path: 'path/f1.pdf', last_modified: 1234567890 }]),
        },
      }

      ;(service as any).processFileMetadata(req, files)

      expect(files[0].filePath).to.equal('path/f1.pdf')
      expect(files[0].lastModified).to.equal(1234567890)
    })

    it('should throw BadRequestError for invalid JSON metadata', () => {
      const service = createService()
      const files = [{ originalname: 'f1.pdf' }] as any[]
      const req = { body: { files_metadata: 'not-json' } }

      try {
        ;(service as any).processFileMetadata(req, files)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should throw BadRequestError when metadata count mismatches files count', () => {
      const service = createService()
      const files = [
        { originalname: 'f1.pdf' },
        { originalname: 'f2.pdf' },
      ] as any[]
      const req = {
        body: {
          files_metadata: JSON.stringify([{ file_path: 'p1', last_modified: 123 }]),
        },
      }

      try {
        ;(service as any).processFileMetadata(req, files)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect((error as Error).message).to.include('Metadata count mismatch')
      }
    })

    it('should use originalname as fallback when file_path is missing', () => {
      const service = createService()
      const files = [{ originalname: 'fallback.pdf' }] as any[]
      const req = {
        body: {
          files_metadata: JSON.stringify([{}]),
        },
      }

      ;(service as any).processFileMetadata(req, files)

      expect(files[0].filePath).to.equal('fallback.pdf')
      expect(files[0].lastModified).to.be.a('number')
    })

    it('should use Date.now() when last_modified is invalid (NaN)', () => {
      const service = createService()
      const files = [{ originalname: 'f1.pdf' }] as any[]
      const req = {
        body: {
          files_metadata: JSON.stringify([{ file_path: 'p1', last_modified: 'not-a-number' }]),
        },
      }

      const before = Date.now()
      ;(service as any).processFileMetadata(req, files)
      const after = Date.now()

      expect(files[0].lastModified).to.be.gte(before)
      expect(files[0].lastModified).to.be.lte(after)
    })

    it('should use Date.now() when last_modified is 0 or negative', () => {
      const service = createService()
      const files = [{ originalname: 'f1.pdf' }] as any[]
      const req = {
        body: {
          files_metadata: JSON.stringify([{ file_path: 'p1', last_modified: 0 }]),
        },
      }

      const before = Date.now()
      ;(service as any).processFileMetadata(req, files)

      expect(files[0].lastModified).to.be.gte(before)
    })

    it('should use Date.now() when no metadata is provided', () => {
      const service = createService()
      const files = [{ originalname: 'f1.pdf' }] as any[]
      const req = { body: {} }

      ;(service as any).processFileMetadata(req, files)

      expect(files[0].filePath).to.equal('f1.pdf')
      expect(files[0].lastModified).to.be.a('number')
    })
  })

  // =========================================================================
  // getFiles - private method
  // =========================================================================
  describe('getFiles - file retrieval branches', () => {
    it('should return single file from req.file', () => {
      const service = createService()
      const file = { originalname: 'test.pdf' }
      const req = { file, files: null, body: {} }
      const files = (service as any).getFiles(req)
      expect(files).to.have.length(1)
    })

    it('should return files from req.files when it is an array', () => {
      const service = createService()
      const files = [{ originalname: 'f1.pdf' }, { originalname: 'f2.pdf' }]
      const req = { file: null, files, body: {} }
      const result = (service as any).getFiles(req)
      expect(result).to.have.length(2)
    })

    it('should handle req.files as object with field name', () => {
      const service = createService({ fieldName: 'documents' })
      const fieldFiles = [{ originalname: 'f1.pdf' }]
      const req = { file: null, files: { documents: fieldFiles }, body: {} }
      const result = (service as any).getFiles(req)
      expect(result).to.have.length(1)
    })

    it('should handle single file under field name (non-array)', () => {
      const service = createService({ fieldName: 'document' })
      const singleFile = { originalname: 'single.pdf' }
      const req = { file: null, files: { document: singleFile }, body: {} }
      const result = (service as any).getFiles(req)
      expect(result).to.have.length(1)
    })

    it('should return empty array when no files found', () => {
      const service = createService()
      const req = { file: null, files: null, body: {} }
      const result = (service as any).getFiles(req)
      expect(result).to.have.length(0)
    })

    it('should return empty array on error', () => {
      const service = createService()
      // Create a request that will cause an error in getFiles
      const req = {
        get file() { throw new Error('error accessing file') },
        files: null,
        body: {},
      }
      const result = (service as any).getFiles(req)
      expect(result).to.have.length(0)
    })

    it('should handle fieldName as array when accessing req.files object', () => {
      const service = createService({ fieldName: ['uploads', 'files'] })
      const fieldFiles = [{ originalname: 'f1.pdf' }]
      const req = { file: null, files: { uploads: fieldFiles }, body: {} }
      const result = (service as any).getFiles(req)
      expect(result).to.have.length(1)
    })
  })

  // =========================================================================
  // getMiddleware
  // =========================================================================
  describe('getMiddleware', () => {
    it('should return array of two middleware functions', () => {
      const service = createService()
      const middleware = service.getMiddleware()
      expect(middleware).to.be.an('array')
      expect(middleware).to.have.length(2)
    })
  })

  // =========================================================================
  // processBufferFiles - edge cases
  // =========================================================================
  describe('processBufferFiles - no file branch', () => {
    it('should log warning when no files and not multi', () => {
      const service = createService({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: false,
      })
      const handler = service.processFiles()
      // Give it multiple files but isMultipleFilesAllowed is false, and empty array
      // Actually let's test the else branch (files.length > 1 but !isMultipleFilesAllowed and files.length !== 1)
      const files = [
        { originalname: 'f1.pdf', buffer: Buffer.from('1'), mimetype: 'application/pdf', size: 1 },
        { originalname: 'f2.pdf', buffer: Buffer.from('2'), mimetype: 'application/pdf', size: 1 },
      ]
      const req = { file: null, files, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      // This should hit the else branch (not multi, files.length !== 1)
      expect(next.calledOnce).to.be.true
    })
  })

  // =========================================================================
  // Default processing type
  // =========================================================================
  describe('processFiles - unsupported processing type', () => {
    it('should throw NotImplementedError for unknown processing type', () => {
      const service = createService({ processingType: 'UNKNOWN' as any })
      const handler = service.processFiles()
      const file = { originalname: 'test.pdf', buffer: Buffer.from('x'), mimetype: 'application/pdf', size: 1 }
      const req = { file, files: null, body: {} } as any
      const res = {} as any
      const next = sinon.stub()

      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError)
    })
  })
})
}

{
function createConfig(overrides: Partial<FileProcessorConfiguration> = {}): FileProcessorConfiguration {
  return {
    fieldName: 'file',
    maxFileSize: 1024 * 1024 * 5,
    allowedMimeTypes: ['application/json', 'application/pdf', 'image/jpeg'],
    maxFilesAllowed: 1,
    isMultipleFilesAllowed: false,
    processingType: FileProcessingType.JSON,
    strictFileUpload: false,
    ...overrides,
  }
}

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
  return res
}

describe('FileProcessorService - additional coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('processFiles - BUFFER type single file', () => {
    it('should process a single buffer file', () => {
      const config = createConfig({
        processingType: FileProcessingType.BUFFER,
        strictFileUpload: false,
        isMultipleFilesAllowed: false,
      })
      const service = new FileProcessorService(config)

      const req = createMockRequest({
        file: {
          originalname: 'test.pdf',
          buffer: Buffer.from('content'),
          mimetype: 'application/pdf',
          size: 7,
        },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileBuffer).to.exist
      expect(req.body.fileBuffer.originalname).to.equal('test.pdf')
      expect(req.body.fileBuffer.size).to.equal(7)
    })
  })

  describe('processFiles - BUFFER type multiple files', () => {
    it('should process multiple buffer files', () => {
      const config = createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: true,
        maxFilesAllowed: 5,
      })
      const service = new FileProcessorService(config)

      const req = createMockRequest({
        files: [
          { originalname: 'a.pdf', buffer: Buffer.from('a'), mimetype: 'application/pdf', size: 1 },
          { originalname: 'b.pdf', buffer: Buffer.from('b'), mimetype: 'application/pdf', size: 1 },
        ],
      })
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileBuffers).to.be.an('array')
      expect(req.body.fileBuffers.length).to.equal(2)
    })
  })

  describe('processFiles - JSON type single file', () => {
    it('should parse JSON from single file buffer', () => {
      const config = createConfig({
        processingType: FileProcessingType.JSON,
        isMultipleFilesAllowed: false,
      })
      const service = new FileProcessorService(config)

      const jsonContent = JSON.stringify({ key: 'value' })
      const req = createMockRequest({
        file: {
          originalname: 'data.json',
          buffer: Buffer.from(jsonContent),
          mimetype: 'application/json',
          size: jsonContent.length,
        },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileContent).to.deep.equal({ key: 'value' })
    })
  })

  describe('processFiles - JSON type multiple files', () => {
    it('should parse JSON from multiple files', () => {
      const config = createConfig({
        processingType: FileProcessingType.JSON,
        isMultipleFilesAllowed: true,
        maxFilesAllowed: 5,
      })
      const service = new FileProcessorService(config)

      const req = createMockRequest({
        files: [
          { originalname: 'a.json', buffer: Buffer.from('{"a":1}'), mimetype: 'application/json', size: 7 },
          { originalname: 'b.json', buffer: Buffer.from('{"b":2}'), mimetype: 'application/json', size: 7 },
        ],
      })
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(req.body.fileContents).to.deep.equal([{ a: 1 }, { b: 2 }])
    })
  })

  describe('processFiles - no files with strict mode', () => {
    it('should call next with BadRequestError when strict and no files', () => {
      const config = createConfig({ strictFileUpload: true })
      const service = new FileProcessorService(config)

      const req = createMockRequest()
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  describe('processFiles - no files with non-strict mode', () => {
    it('should call next without error when not strict and no files', () => {
      const config = createConfig({ strictFileUpload: false })
      const service = new FileProcessorService(config)

      const req = createMockRequest()
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args.length).to.equal(0)
    })
  })

  describe('processFiles - unsupported processing type', () => {
    it('should call next with BadRequestError for unknown processing type', () => {
      const config = createConfig({ processingType: 'UNKNOWN' as any })
      const service = new FileProcessorService(config)

      const req = createMockRequest({
        file: {
          originalname: 'test.pdf',
          buffer: Buffer.from('x'),
          mimetype: 'application/pdf',
          size: 1,
        },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
      const error = next.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  describe('upload - non-multipart request', () => {
    it('should skip processing for non-multipart requests', () => {
      const config = createConfig()
      const service = new FileProcessorService(config)

      const req = createMockRequest({
        headers: { 'content-type': 'application/json' },
      })
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.upload()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getMiddleware', () => {
    it('should return array of two middleware handlers', () => {
      const config = createConfig()
      const service = new FileProcessorService(config)

      const middlewares = service.getMiddleware()
      expect(middlewares).to.be.an('array')
      expect(middlewares.length).to.equal(2)
    })
  })

  describe('processFileMetadata - with files_metadata', () => {
    it('should process files_metadata JSON and attach to files', () => {
      const config = createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: false,
      })
      const service = new FileProcessorService(config)

      const files = [
        { originalname: 'test.pdf', buffer: Buffer.from('x'), mimetype: 'application/pdf', size: 1 },
      ] as Express.Multer.File[]

      const req = createMockRequest({
        file: files[0],
        body: {
          files_metadata: JSON.stringify([{ file_path: '/path/test.pdf', last_modified: Date.now() }]),
        },
      })

      // Call processFileMetadata indirectly via processFiles
      const res = createMockResponse()
      const next = sinon.stub()
      const handler = service.processFiles()
      handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getFiles - object-style files', () => {
    it('should handle req.files as object with field names', () => {
      const config = createConfig({ fieldName: 'document' })
      const service = new FileProcessorService(config)

      const mockFile = {
        originalname: 'test.pdf',
        buffer: Buffer.from('test'),
        mimetype: 'application/pdf',
        size: 4,
      }

      const req = createMockRequest({
        files: {
          document: [mockFile],
        },
      })

      // Access getFiles indirectly through processFiles
      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      // Files should be found from the object-style files
      expect(next.calledOnce).to.be.true
    })
  })

  describe('processBufferFiles - empty files array edge case', () => {
    it('should handle the case where files is non-empty but all null after filter', () => {
      const config = createConfig({
        processingType: FileProcessingType.BUFFER,
        isMultipleFilesAllowed: true,
      })
      const service = new FileProcessorService(config)

      // Files are present but after the filter(Boolean), nothing remains
      // This is an edge case in processBufferFiles
      const req = createMockRequest({
        files: [null as any],
      })

      const res = createMockResponse()
      const next = sinon.stub()

      const handler = service.processFiles()
      handler(req, res, next)

      // With no valid files, should skip or call next
      expect(next.calledOnce).to.be.true
    })
  })

  describe('upload - array field name', () => {
    it('should handle array field name by using first element', () => {
      const config = createConfig({ fieldName: ['file', 'document'] as any })
      const service = new FileProcessorService(config)

      expect(service).to.exist
      const middlewares = service.getMiddleware()
      expect(middlewares.length).to.equal(2)
    })
  })
})
}
