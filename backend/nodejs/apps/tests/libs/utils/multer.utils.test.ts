import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { PassThrough } from 'stream'
import type { NextFunction, Request, RequestHandler, Response } from 'express'
import multer from 'multer'
import { readFileSync } from 'fs'
import { dirname, join } from 'path'
import { BUSBOY_PARSE_ERRORS, createMulter, isUploadLimit } from '../../../src/libs/utils/multer.utils'
import { FileProcessorService } from '../../../src/libs/middlewares/file_processor/fp.service'
import { FileProcessingType } from '../../../src/libs/middlewares/file_processor/fp.constant'
import { Logger } from '../../../src/libs/services/logger.service'

const NAME = 'résumé 日本語 🎉.txt'
const BOUNDARY = 'pipeshub-test-boundary'

// Just enough of an Express request for multer, which streams the body itself.
type MultipartRequest = PassThrough &
  Pick<Request, 'headers' | 'body' | 'method'> & {
    file?: Express.Multer.File
    files?: Express.Multer.File[]
  }

// A request as a browser sends it: the filename parameter is raw UTF-8 and
// there is no separate path field.
function multipartRequest(field: string, filename: string): MultipartRequest {
  const body = Buffer.from(
    `--${BOUNDARY}\r\n` +
      `Content-Disposition: form-data; name="${field}"; filename="${filename}"\r\n` +
      'Content-Type: text/plain\r\n\r\n' +
      'hello\r\n' +
      `--${BOUNDARY}--\r\n`,
    'utf8',
  )
  const req = Object.assign(new PassThrough(), {
    headers: {
      'content-type': `multipart/form-data; boundary=${BOUNDARY}`,
      'content-length': String(body.length),
    },
    body: {},
    method: 'POST',
  })
  req.end(body)
  return req
}

function run(handler: RequestHandler, req: MultipartRequest): Promise<void> {
  return new Promise((resolve, reject) => {
    const next: NextFunction = (err?: unknown) => (err ? reject(err) : resolve())
    handler(req as unknown as Request, {} as Response, next)
  })
}

describe('createMulter', () => {
  it('keeps a UTF-8 filename intact', async () => {
    const req = multipartRequest('file', NAME)
    await run(createMulter({ storage: multer.memoryStorage() }).single('file'), req)
    expect(req.file?.originalname).to.equal(NAME)
  })

  it('differs from bare multer, which decodes the name as Latin-1', async () => {
    const req = multipartRequest('file', NAME)
    await run(multer({ storage: multer.memoryStorage() }).single('file'), req)
    expect(req.file?.originalname).to.not.equal(NAME)
  })
})

describe('FileProcessorService upload keeps non-ASCII names', () => {
  beforeEach(() => {
    const logger = Logger.getInstance()
    sinon.stub(logger, 'error')
    sinon.stub(logger, 'warn')
    sinon.stub(logger, 'debug')
    sinon.stub(logger, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  for (const partialUpload of [true, false]) {
    it(`with partialUpload=${partialUpload}`, async () => {
      const service = new FileProcessorService({
        fieldName: 'files',
        maxFileSize: 1024 * 1024,
        allowedMimeTypes: ['text/plain'],
        allowedExtensions: ['txt'],
        maxFilesAllowed: 10,
        isMultipleFilesAllowed: true,
        processingType: FileProcessingType.BUFFER,
        strictFileUpload: true,
        partialUpload,
      })
      const req = multipartRequest('files', NAME)
      await run(service.upload(), req)
      expect(req.files?.map((f) => f.originalname)).to.deep.equal([NAME])
    })
  }
})

describe('isUploadLimit', () => {
  for (const code of ['LIMIT_FILE_SIZE', 'LIMIT_FILE_COUNT', 'LIMIT_PART_COUNT', 'LIMIT_FIELD_KEY', 'LIMIT_FIELD_VALUE', 'LIMIT_FIELD_COUNT']) {
    it(`takes ${code} for an upload past a limit`, () => {
      expect(isUploadLimit(code)).to.equal(true)
    })
  }

  for (const code of ['LIMIT_UNEXPECTED_FILE', 'MISSING_FIELD_NAME', 'INVALID_FIELD_NAME', 'STREAM_DESTROYED', 'ECONNRESET', '', undefined, 413]) {
    it(`does not take ${String(code)} for one`, () => {
      expect(isUploadLimit(code)).to.equal(false)
    })
  }
})

describe('BUSBOY_PARSE_ERRORS', () => {
  // Upgrade guard: busboy's parse errors have no code, so a renamed message would silently fall back to a 500.
  it('each message is still one the installed busboy throws', () => {
    const lib = join(dirname(require.resolve('busboy/package.json')), 'lib')
    const source = ['index.js', 'types/multipart.js', 'types/urlencoded.js'].map((f) => readFileSync(join(lib, f), 'utf8')).join('\n')
    for (const message of [...BUSBOY_PARSE_ERRORS, 'Unsupported content type: ']) {
      expect(source, message).to.include(message)
    }
  })
})
