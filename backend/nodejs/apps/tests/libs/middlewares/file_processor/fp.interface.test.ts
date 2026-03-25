import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FileProcessingType } from '../../../../src/libs/middlewares/file_processor/fp.constant'

describe('libs/middlewares/file_processor/fp.interface', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/libs/middlewares/file_processor/fp.interface')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/libs/middlewares/file_processor/fp.interface')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('FileProcessorConfiguration interface', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../src/libs/middlewares/file_processor/fp.interface').FileProcessorConfiguration = {
        fieldName: 'file',
        maxFileSize: 1024 * 1024 * 100,
        allowedMimeTypes: ['application/pdf', 'image/png'],
        maxFilesAllowed: 5,
        isMultipleFilesAllowed: true,
        processingType: FileProcessingType.BUFFER,
        strictFileUpload: true,
      }
      expect(config.fieldName).to.equal('file')
      expect(config.maxFileSize).to.equal(104857600)
      expect(config.allowedMimeTypes).to.have.lengthOf(2)
      expect(config.maxFilesAllowed).to.equal(5)
      expect(config.isMultipleFilesAllowed).to.be.true
      expect(config.strictFileUpload).to.be.true
    })
  })

  describe('FileBufferInfo interface', () => {
    it('should allow creating conforming objects', () => {
      const info: import('../../../../src/libs/middlewares/file_processor/fp.interface').FileBufferInfo = {
        buffer: Buffer.from('test'),
        originalname: 'test.pdf',
        mimetype: 'application/pdf',
        size: 1024,
        lastModified: Date.now(),
        filePath: '/tmp/test.pdf',
      }
      expect(info.buffer).to.be.instanceOf(Buffer)
      expect(info.originalname).to.equal('test.pdf')
      expect(info.mimetype).to.equal('application/pdf')
      expect(info.size).to.equal(1024)
      expect(info.filePath).to.equal('/tmp/test.pdf')
    })
  })

  describe('IFileUploadService interface', () => {
    it('should allow implementing the interface', () => {
      const mockService: import('../../../../src/libs/middlewares/file_processor/fp.interface').IFileUploadService = {
        upload: () => (() => {}) as any,
        processFiles: () => (() => {}) as any,
        getMiddleware: () => [],
      }
      expect(mockService.upload).to.be.a('function')
      expect(mockService.processFiles).to.be.a('function')
      expect(mockService.getMiddleware).to.be.a('function')
    })
  })
})
