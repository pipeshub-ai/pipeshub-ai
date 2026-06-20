import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FileProcessorService } from '../../../../src/libs/middlewares/file_processor/fp.service'
import { FileProcessorConfiguration } from '../../../../src/libs/middlewares/file_processor/fp.interface'
import {
  FileProcessingType,
  FileRejectionReason,
} from '../../../../src/libs/middlewares/file_processor/fp.constant'
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

const ONE_MB = 1024 * 1024

function createPartialConfig(
  overrides: Partial<FileProcessorConfiguration> = {},
): FileProcessorConfiguration {
  return {
    fieldName: 'files',
    maxFileSize: ONE_MB,
    // Include a generic MIME so we can prove an allowed-but-generic MIME does
    // NOT smuggle in an unsupported-extension file (the `.DS_Store` leak).
    allowedMimeTypes: ['application/pdf', 'text/plain', 'application/octet-stream'],
    allowedExtensions: ['pdf', 'txt'],
    maxFilesAllowed: 100,
    isMultipleFilesAllowed: true,
    processingType: FileProcessingType.BUFFER,
    strictFileUpload: true,
    partialUpload: true,
    ...overrides,
  }
}

// A file in its post-metadata state (filePath set, as it is by the time
// processBufferFiles runs in the real middleware chain).
function makeFile(
  originalname: string,
  filePath: string,
  size: number,
  mimetype = 'application/pdf',
): any {
  return {
    buffer: Buffer.alloc(Math.min(size, 16)),
    originalname,
    mimetype,
    size,
    filePath,
  }
}

function namesOf(rejected: any[]): string[] {
  return rejected.map((r) => r.filePath)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('FileProcessorService - partial upload type & size rejection', () => {
  beforeEach(() => {
    const loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  it('rejects an unsupported extension with a stable reason code and keeps valid files', () => {
    const service = new FileProcessorService(createPartialConfig())
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [
        makeFile('ok.pdf', 'KB/ok.pdf', 10),
        makeFile('bad.exe', 'KB/bad.exe', 10, 'application/octet-stream'),
      ],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(next.calledOnce).to.equal(true)
    expect(next.firstCall.args).to.have.length(0) // batch not aborted

    expect(req.body.fileBuffers).to.have.length(1)
    expect(req.body.fileBuffers[0].originalname).to.equal('ok.pdf')

    expect(req.body.rejectedFiles).to.have.length(1)
    expect(req.body.rejectedFiles[0].originalname).to.equal('bad.exe')
    expect(req.body.rejectedFiles[0].reason).to.equal(
      FileRejectionReason.UNSUPPORTED_TYPE,
    )
  })

  it('does NOT let an allowed-but-generic MIME smuggle in an unsupported extension (.DS_Store leak)', () => {
    const service = new FileProcessorService(createPartialConfig())
    const handler = service.processFiles()

    // `.DS_Store`: browser reports a generic MIME that is technically in the
    // allowed set, but the extension is not supported — must be rejected.
    const req = createMockRequest({
      files: [makeFile('.DS_Store', 'KB/.DS_Store', 100, 'application/octet-stream')],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(0)
    expect(req.body.rejectedFiles).to.have.length(1)
    expect(req.body.rejectedFiles[0].reason).to.equal(
      FileRejectionReason.UNSUPPORTED_TYPE,
    )
  })

  it('keeps duplicate unsupported filenames distinct by their resolved filePath', () => {
    const service = new FileProcessorService(createPartialConfig())
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [
        makeFile('.DS_Store', 'KB/.DS_Store', 10, 'application/octet-stream'),
        makeFile('.DS_Store', 'KB/Offers/.DS_Store', 10, 'application/octet-stream'),
        makeFile('a.pdf', 'KB/a.pdf', 10),
      ],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(1)
    expect(req.body.rejectedFiles).to.have.length(2)
    // The two rejections carry their full, distinct paths (so the client can
    // map each failure back to the right row).
    expect(namesOf(req.body.rejectedFiles)).to.deep.equal([
      'KB/.DS_Store',
      'KB/Offers/.DS_Store',
    ])
  })

  it('reports type and size failures together with the correct reason for each', () => {
    const service = new FileProcessorService(createPartialConfig())
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [
        makeFile('bad.exe', 'KB/bad.exe', 10, 'application/octet-stream'),
        makeFile('huge.pdf', 'KB/huge.pdf', 2 * ONE_MB),
        makeFile('ok.txt', 'KB/ok.txt', 10, 'text/plain'),
      ],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(1)
    expect(req.body.fileBuffers[0].originalname).to.equal('ok.txt')

    const byPath = Object.fromEntries(
      req.body.rejectedFiles.map((r: any) => [r.filePath, r.reason]),
    )
    expect(byPath['KB/bad.exe']).to.equal(FileRejectionReason.UNSUPPORTED_TYPE)
    expect(byPath['KB/huge.pdf']).to.equal(
      FileRejectionReason.EXCEEDS_SIZE_LIMIT,
    )
  })

  it('reports no rejections for an all-valid batch', () => {
    const service = new FileProcessorService(createPartialConfig())
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [
        makeFile('a.pdf', 'KB/a.pdf', 10),
        makeFile('b.txt', 'KB/b.txt', 20, 'text/plain'),
      ],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(2)
    expect(req.body.rejectedFiles).to.have.length(0)
  })
})

// ---------------------------------------------------------------------------
// effectiveMimeType, rejectionMessage, and partial-upload edge case coverage
// ---------------------------------------------------------------------------

describe('FileProcessorService - effectiveMimeType and resolveMimeType', () => {
  beforeEach(() => {
    const loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  it('uses resolveMimeType when browser MIME is not in the allowed list', () => {
    const service = new FileProcessorService(createPartialConfig({
      allowedMimeTypes: ['application/pdf', 'text/markdown'],
      allowedExtensions: ['pdf', 'md'],
      resolveMimeType: (ext: string) => {
        if (ext === 'md') return 'text/markdown'
        return null
      },
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('notes.md', 'KB/notes.md', 10, 'application/octet-stream')],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(1)
    expect(req.body.fileBuffers[0].mimetype).to.equal('text/markdown')
  })

  it('falls back to browser MIME when resolveMimeType returns null', () => {
    const service = new FileProcessorService(createPartialConfig({
      allowedMimeTypes: ['application/pdf', 'text/plain', 'application/octet-stream'],
      allowedExtensions: ['pdf', 'txt', 'xyz'],
      resolveMimeType: () => null,
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('data.xyz', 'KB/data.xyz', 10, 'application/octet-stream')],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(1)
    expect(req.body.fileBuffers[0].mimetype).to.equal('application/octet-stream')
  })

  it('uses browser MIME directly when it is in the allowed list', () => {
    const service = new FileProcessorService(createPartialConfig({
      resolveMimeType: () => 'text/plain',
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('a.pdf', 'KB/a.pdf', 10, 'application/pdf')],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers[0].mimetype).to.equal('application/pdf')
  })

  it('falls back to browser MIME when no resolveMimeType is configured', () => {
    const service = new FileProcessorService(createPartialConfig({
      allowedMimeTypes: ['application/pdf', 'text/csv'],
      allowedExtensions: ['pdf', 'csv'],
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('data.csv', 'KB/data.csv', 10, '')],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(1)
    expect(req.body.fileBuffers[0].mimetype).to.equal('')
  })
})

describe('FileProcessorService - isAllowedType MIME fallback', () => {
  beforeEach(() => {
    const loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  it('falls back to MIME type check when no extension allowlist is configured', () => {
    const service = new FileProcessorService(createPartialConfig({
      allowedExtensions: undefined,
      allowedMimeTypes: ['application/pdf', 'text/plain'],
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [
        makeFile('a.pdf', 'KB/a.pdf', 10, 'application/pdf'),
        makeFile('b.exe', 'KB/b.exe', 10, 'application/x-msdownload'),
      ],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(1)
    expect(req.body.fileBuffers[0].originalname).to.equal('a.pdf')
    expect(req.body.rejectedFiles).to.have.length(1)
    expect(req.body.rejectedFiles[0].reason).to.equal(FileRejectionReason.UNSUPPORTED_TYPE)
  })

  it('falls back to MIME check when extension allowlist is empty array', () => {
    const service = new FileProcessorService(createPartialConfig({
      allowedExtensions: [],
      allowedMimeTypes: ['text/plain'],
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('readme.txt', 'KB/readme.txt', 5, 'text/plain')],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffers).to.have.length(1)
  })
})

describe('FileProcessorService - partial upload with all files rejected', () => {
  beforeEach(() => {
    const loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  it('sets empty fileBuffers and populates rejectedFiles when all files are rejected', () => {
    const service = new FileProcessorService(createPartialConfig({
      strictFileUpload: true,
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [
        makeFile('a.exe', 'KB/a.exe', 10, 'application/octet-stream'),
        makeFile('b.bat', 'KB/b.bat', 10, 'application/octet-stream'),
      ],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(next.calledOnce).to.equal(true)
    expect(next.firstCall.args).to.have.length(0)
    expect(req.body.fileBuffers).to.have.length(0)
    expect(req.body.rejectedFiles).to.have.length(2)
  })

  it('handles no files with partial upload and rejections on __fpRejectedFiles', () => {
    const service = new FileProcessorService(createPartialConfig({
      strictFileUpload: true,
    }))
    const handler = service.processFiles()

    const req = createMockRequest({ files: [] })
    ;(req as any).__fpRejectedFiles = [
      {
        originalname: 'bad.exe',
        filePath: 'KB/bad.exe',
        size: 10,
        mimetype: 'application/octet-stream',
        reason: FileRejectionReason.UNSUPPORTED_TYPE,
        error: 'Unsupported file type ".exe"',
      },
    ]
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(next.calledOnce).to.equal(true)
    expect(next.firstCall.args).to.have.length(0)
    expect(req.body.rejectedFiles).to.have.length(1)
    expect(req.body.fileBuffers).to.have.length(0)
  })
})

describe('FileProcessorService - rejectionMessage edge cases', () => {
  beforeEach(() => {
    const loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  it('reports "Unsupported file type" without extension for dotfiles', () => {
    const service = new FileProcessorService(createPartialConfig())
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('.hidden', 'KB/.hidden', 10, 'application/octet-stream')],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.rejectedFiles).to.have.length(1)
    expect(req.body.rejectedFiles[0].error).to.equal('Unsupported file type')
  })

  it('reports EXCEEDS_SIZE_LIMIT with correct MB in the message', () => {
    const service = new FileProcessorService(createPartialConfig({
      maxFileSize: 5 * ONE_MB,
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('huge.pdf', 'KB/huge.pdf', 6 * ONE_MB)],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.rejectedFiles).to.have.length(1)
    expect(req.body.rejectedFiles[0].error).to.equal('File exceeds the 5 MB size limit')
  })

  it('uses originalname as fallback when filePath is not set on file', () => {
    const service = new FileProcessorService(createPartialConfig())
    const handler = service.processFiles()

    const file = {
      buffer: Buffer.alloc(16),
      originalname: 'bad.exe',
      mimetype: 'application/octet-stream',
      size: 10,
    }
    const req = createMockRequest({ files: [file] })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.rejectedFiles).to.have.length(1)
    expect(req.body.rejectedFiles[0].filePath).to.equal('bad.exe')
  })
})

describe('FileProcessorService - single file buffer in partial mode', () => {
  beforeEach(() => {
    const loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  it('sets fileBuffer (not fileBuffers) when isMultipleFilesAllowed is false in partial mode', () => {
    const service = new FileProcessorService(createPartialConfig({
      isMultipleFilesAllowed: false,
    }))
    const handler = service.processFiles()

    const req = createMockRequest({
      files: [makeFile('a.pdf', 'KB/a.pdf', 10)],
    })
    const res = createMockResponse()
    const next = createMockNext()

    handler(req, res, next)

    expect(req.body.fileBuffer).to.exist
    expect(req.body.fileBuffer.originalname).to.equal('a.pdf')
    expect(req.body.rejectedFiles).to.have.length(0)
  })
})
