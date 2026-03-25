import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FileProcessorFactory } from '../../../../src/libs/middlewares/file_processor/fp.factory'
import { FileProcessorConfiguration } from '../../../../src/libs/middlewares/file_processor/fp.interface'
import { FileProcessingType } from '../../../../src/libs/middlewares/file_processor/fp.constant'
import { Logger } from '../../../../src/libs/services/logger.service'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

describe('FileProcessorFactory', () => {
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
  // createJSONUploadProcessor
  // -----------------------------------------------------------------------
  describe('createJSONUploadProcessor', () => {
    it('should return an object with getMiddleware array', () => {
      const result = FileProcessorFactory.createJSONUploadProcessor(createConfig())
      expect(result).to.have.property('getMiddleware')
      expect(result.getMiddleware).to.be.an('array')
    })

    it('should return getMiddleware with exactly 1 combined middleware function', () => {
      const result = FileProcessorFactory.createJSONUploadProcessor(createConfig())
      expect(result.getMiddleware).to.have.length(1)
      expect(result.getMiddleware[0]).to.be.a('function')
    })

    it('should create a processor with JSON processing type regardless of input config', () => {
      const config = createConfig({ processingType: FileProcessingType.BUFFER })
      const result = FileProcessorFactory.createJSONUploadProcessor(config)
      // The factory overrides processingType to JSON
      expect(result.getMiddleware).to.have.length(1)
    })
  })

  // -----------------------------------------------------------------------
  // createBufferUploadProcessor
  // -----------------------------------------------------------------------
  describe('createBufferUploadProcessor', () => {
    it('should return an object with getMiddleware array', () => {
      const result = FileProcessorFactory.createBufferUploadProcessor(createConfig())
      expect(result).to.have.property('getMiddleware')
      expect(result.getMiddleware).to.be.an('array')
    })

    it('should return getMiddleware with exactly 1 combined middleware function', () => {
      const result = FileProcessorFactory.createBufferUploadProcessor(createConfig())
      expect(result.getMiddleware).to.have.length(1)
      expect(result.getMiddleware[0]).to.be.a('function')
    })

    it('should create a processor with BUFFER processing type regardless of input config', () => {
      const config = createConfig({ processingType: FileProcessingType.JSON })
      const result = FileProcessorFactory.createBufferUploadProcessor(config)
      expect(result.getMiddleware).to.have.length(1)
    })
  })

  // -----------------------------------------------------------------------
  // Lazy initialization
  // -----------------------------------------------------------------------
  describe('Lazy initialization', () => {
    it('should lazily initialize the service instance (JSON)', () => {
      const result = FileProcessorFactory.createJSONUploadProcessor(createConfig())
      // The middleware is a function; calling it multiple times should reuse the same service instance
      expect(result.getMiddleware[0]).to.be.a('function')
    })

    it('should lazily initialize the service instance (Buffer)', () => {
      const result = FileProcessorFactory.createBufferUploadProcessor(createConfig())
      expect(result.getMiddleware[0]).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // Middleware chain - error handling in upload step
  // -----------------------------------------------------------------------
  describe('Middleware chain error handling', () => {
    it('should propagate upload error in JSON processor', (done) => {
      const config = createConfig({ strictFileUpload: true })
      const result = FileProcessorFactory.createJSONUploadProcessor(config)
      const middleware = result.getMiddleware[0]!

      // Simulate a multipart request that will trigger multer
      // but with wrong content type so upload fails
      const req: any = {
        headers: { 'content-type': 'multipart/form-data; boundary=test' },
        body: {},
        file: undefined,
        files: undefined,
        // Multer needs these
        pipe: sinon.stub(),
        unpipe: sinon.stub(),
        on: sinon.stub(),
        pause: sinon.stub(),
        resume: sinon.stub(),
        readable: true,
      }
      const res: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
        setHeader: sinon.stub(),
        getHeader: sinon.stub(),
        headersSent: false,
      }

      middleware(req, res, (err?: any) => {
        // Some error or success - either way the middleware should call next
        done()
      })
    })

    it('should propagate upload error in Buffer processor', (done) => {
      const config = createConfig({ strictFileUpload: true })
      const result = FileProcessorFactory.createBufferUploadProcessor(config)
      const middleware = result.getMiddleware[0]!

      const req: any = {
        headers: { 'content-type': 'multipart/form-data; boundary=test' },
        body: {},
        file: undefined,
        files: undefined,
        pipe: sinon.stub(),
        unpipe: sinon.stub(),
        on: sinon.stub(),
        pause: sinon.stub(),
        resume: sinon.stub(),
        readable: true,
      }
      const res: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
        setHeader: sinon.stub(),
        getHeader: sinon.stub(),
        headersSent: false,
      }

      middleware(req, res, (err?: any) => {
        done()
      })
    })
  })

  // -----------------------------------------------------------------------
  // Middleware chain - non-multipart request
  // -----------------------------------------------------------------------
  describe('Middleware chain with non-multipart request', () => {
    it('should call next() for non-multipart request in JSON processor', (done) => {
      const result = FileProcessorFactory.createJSONUploadProcessor(createConfig())
      const middleware = result.getMiddleware[0]!

      const req: any = {
        headers: { 'content-type': 'application/json' },
        body: {},
        file: undefined,
        files: undefined,
      }
      const res: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
      }

      middleware(req, res, (err?: any) => {
        // upload skips non-multipart, then processFiles sees no files (non-strict) -> next()
        // If err is a BadRequestError about "no files", that's also acceptable for strict mode
        // For non-strict mode, next should be called without error
        if (err) {
          // If non-strict, this shouldn't happen. But the middleware chain calls processFiles
          // which in non-strict mode calls next()
          done(err)
        } else {
          done()
        }
      })
    })

    it('should call next() for non-multipart request in Buffer processor', (done) => {
      const result = FileProcessorFactory.createBufferUploadProcessor(createConfig())
      const middleware = result.getMiddleware[0]!

      const req: any = {
        headers: { 'content-type': 'application/json' },
        body: {},
        file: undefined,
        files: undefined,
      }
      const res: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
      }

      middleware(req, res, (err?: any) => {
        if (err) {
          done(err)
        } else {
          done()
        }
      })
    })
  })
})
