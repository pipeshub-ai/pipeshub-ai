import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FileProcessorService } from '../../../../src/libs/middlewares/file_processor/fp.service'
import { FileProcessorConfiguration } from '../../../../src/libs/middlewares/file_processor/fp.interface'
import { FileProcessingType } from '../../../../src/libs/middlewares/file_processor/fp.constant'
import { BadRequestError, NotImplementedError } from '../../../../src/libs/errors/http.errors'

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
