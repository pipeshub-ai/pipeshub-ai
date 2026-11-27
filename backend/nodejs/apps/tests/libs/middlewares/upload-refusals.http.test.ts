// A refused multipart upload, end to end over a socket: real express, real multer (createMulter or
// FileProcessorService) and the real ErrorMiddleware. Past a limit is a 413, sent wrong is a 400, never a 500.
import 'reflect-metadata'
import { once } from 'events'
import { AddressInfo } from 'net'
import http from 'http'
import express from 'express'
import multer from 'multer'
import { expect } from 'chai'
import sinon from 'sinon'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { createMulter } from '../../../src/libs/utils/multer.utils'
import { FileProcessorService } from '../../../src/libs/middlewares/file_processor/fp.service'
import { FileProcessingType } from '../../../src/libs/middlewares/file_processor/fp.constant'
import type { FileProcessorConfiguration } from '../../../src/libs/middlewares/file_processor/fp.interface'
import { Logger } from '../../../src/libs/services/logger.service'

type Answer = { status: number; body: any; text: string }
type Server = { url: string; close: () => void }

const TOO_LARGE = "The upload is too large: a file, or the number of files or fields, is over this route's limit."
const UNREADABLE = 'The upload could not be read. Send it as multipart/form-data with the expected file field.'

async function serve(app: express.Express): Promise<Server> {
  app.use(ErrorMiddleware.handleError())
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const { port } = server.address() as AddressInfo
  return { url: `http://127.0.0.1:${port}/up`, close: () => server.close() }
}

async function send(url: string, body: FormData | string, headers: Record<string, string> = {}): Promise<Answer> {
  const r = await fetch(url, { method: 'POST', body, headers })
  const text = await r.text()
  let parsed: any = text
  try {
    parsed = JSON.parse(text)
  } catch {
    /* not JSON */
  }
  return { status: r.status, body: parsed, text }
}

const form = (files: Array<[string, number]>, fields: Array<[string, string]> = []): FormData => {
  const f = new FormData()
  for (const [name, value] of fields) f.append(name, value)
  for (const [field, size] of files) f.append(field, new Blob([Buffer.alloc(size, 'a')]), `${field}.txt`)
  return f
}

const truncated = (field: string): string =>
  `--XyZ\r\nContent-Disposition: form-data; name="${field}"; filename="a.txt"\r\n\r\nabc`

describe('upload refusals over HTTP', () => {
  let logger: Record<'error' | 'warn' | 'info' | 'debug', sinon.SinonStub>
  let originalLogger: unknown

  beforeEach(() => {
    originalLogger = (ErrorMiddleware as any).logger
    logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() }
    ;(ErrorMiddleware as any).logger = logger
  })

  afterEach(() => {
    ;(ErrorMiddleware as any).logger = originalLogger
  })

  describe('a route built with createMulter', () => {
    let srv: Server
    before(async () => {
      const app = express()
      // As skills.routes.ts and openai_compat.routes.ts build it: memory storage, a size and count limit.
      const upload = createMulter({ storage: multer.memoryStorage(), limits: { fileSize: 64, files: 1, fields: 2 } })
      app.post('/up', upload.single('file'), (_req, res) => {
        res.json({ ok: true })
      })
      srv = await serve(app)
    })
    after(() => srv.close())

    it('answers a file over the size limit with 413, logged at warn', async () => {
      const r = await send(srv.url, form([['file', 1000]]))
      expect(r.status).to.equal(413)
      expect(r.body.error).to.include({ code: 'HTTP_PAYLOAD_TOO_LARGE', message: TOO_LARGE })
      expect(logger.error.called).to.equal(false)
      expect(logger.warn.firstCall.args[1]).to.include({ status: 413, error_class: 'bad_request' })
    })

    it("logs multer's code as the cause and leaves it out of the answer", async () => {
      const tooLarge = await send(srv.url, form([['file', 1000]]))
      const wrongPart = await send(srv.url, form([['wrongfieldxyz', 1]]))
      expect(logger.warn.firstCall.args[1].error.cause).to.deep.equal({ code: 'LIMIT_FILE_SIZE' })
      expect(logger.warn.secondCall.args[1].error.cause).to.deep.equal({ code: 'LIMIT_UNEXPECTED_FILE' })
      expect(tooLarge.text).to.not.include('LIMIT_')
      expect(wrongPart.text).to.not.include('LIMIT_')
    })

    it('answers more files than allowed with 413', async () => {
      const r = await send(srv.url, form([['file', 1], ['file', 1]]))
      expect(r.status).to.equal(413)
    })

    it('answers more fields than allowed with 413', async () => {
      const r = await send(srv.url, form([['file', 1]], [['a', '1'], ['b', '2'], ['c', '3']]))
      expect(r.status).to.equal(413)
    })

    it('answers a file under another part name with 400, without naming it', async () => {
      const r = await send(srv.url, form([['wrongfieldxyz', 1]]))
      expect(r.status).to.equal(400)
      expect(r.body.error).to.include({ code: 'HTTP_BAD_REQUEST', message: UNREADABLE })
      expect(r.text).to.not.include('wrongfieldxyz')
    })

    it('answers a body cut short with 400', async () => {
      const r = await send(srv.url, truncated('file'), { 'content-type': 'multipart/form-data; boundary=XyZ' })
      expect(r.status).to.equal(400)
      expect(r.body.error.message).to.equal(UNREADABLE)
    })

    it('answers multipart without a boundary with 400', async () => {
      const r = await send(srv.url, 'x', { 'content-type': 'multipart/form-data' })
      expect(r.status).to.equal(400)
    })

    it('answers a multipart type busboy does not read with 400, without quoting the header', async () => {
      const r = await send(srv.url, 'x', { 'content-type': 'multipart/mixed; boundary=SECRET123' })
      expect(r.status).to.equal(400)
      expect(r.text).to.not.include('SECRET123')
    })

    it('still stores a good upload', async () => {
      const r = await send(srv.url, form([['file', 10]]))
      expect(r.status).to.equal(200)
    })
  })

  describe('a route behind FileProcessorService', () => {
    const base: FileProcessorConfiguration = {
      fieldName: 'files',
      maxFileSize: 64,
      allowedMimeTypes: ['text/plain', 'application/octet-stream'],
      maxFilesAllowed: 2,
      isMultipleFilesAllowed: true,
      processingType: FileProcessingType.BUFFER,
      strictFileUpload: true,
    }

    // The middleware logs the refusal once at warn; the service must not add a warn or error line of its own.
    let serviceErrors: sinon.SinonSpy
    let serviceWarnings: sinon.SinonSpy
    beforeEach(() => {
      const serviceLogger = Logger.getInstance({ service: 'FileProcessorService' })
      serviceErrors = sinon.spy(serviceLogger, 'error')
      serviceWarnings = sinon.spy(serviceLogger, 'warn')
    })
    afterEach(() => {
      serviceErrors.restore()
      serviceWarnings.restore()
    })

    async function serveService(config: FileProcessorConfiguration): Promise<Server> {
      const service = new FileProcessorService(config)
      const app = express()
      app.post('/up', ...service.getMiddleware(), (_req, res) => {
        res.json({ ok: true })
      })
      return serve(app)
    }

    describe('whole-batch mode', () => {
      let srv: Server
      before(async () => {
        srv = await serveService(base)
      })
      after(() => srv.close())

      it('answers a file over maxFileSize with 413 and its plain sentence', async () => {
        const r = await send(srv.url, form([['files', 1000]]))
        expect(r.status).to.equal(413)
        expect(r.body.error.message).to.equal(
          'One of the files is larger than the 0 MB limit. Remove it or make it smaller, then upload again.',
        )
        expect(serviceErrors.called).to.equal(false)
        expect(serviceWarnings.called).to.equal(false)
        expect(logger.warn.calledOnce).to.equal(true)
        expect(logger.warn.firstCall.args[1]).to.include({ status: 413, error_class: 'bad_request' })
      })

      it('answers more files than maxFilesAllowed with 413 and its plain sentence', async () => {
        const r = await send(srv.url, form([['files', 1], ['files', 1], ['files', 1]]))
        expect(r.status).to.equal(413)
        expect(r.body.error.message).to.equal('You can upload up to 2 files at a time. Upload the rest in another batch.')
      })

      it('keeps the meter 413 and its words for a request over the byte cap', async () => {
        // cap = files x fileSize + 16 MiB fields allowance; a declared length past it is refused up front.
        const answer = await new Promise<Answer>((resolve, reject) => {
          const req = http.request(
            srv.url,
            {
              method: 'POST',
              headers: {
                'content-type': 'multipart/form-data; boundary=XyZ',
                'content-length': String(2 * 64 + 16 * 1024 * 1024 + 1),
              },
            },
            (res) => {
              let text = ''
              res.on('data', (c: Buffer) => (text += c.toString()))
              res.on('end', () => resolve({ status: res.statusCode ?? -1, body: JSON.parse(text), text }))
            },
          )
          req.on('error', reject)
          req.write('--XyZ\r\n')
        })
        expect(answer.status).to.equal(413)
        expect(answer.body.error.message).to.match(/^The upload is larger than \d+ MiB\. Send fewer or smaller files\.$/)
      })

      it('keeps a file under another part name at 400 with its plain sentence', async () => {
        const r = await send(srv.url, form([['wrong', 1]]))
        expect(r.status).to.equal(400)
        expect(r.body.error.message).to.equal(
          "The files weren't sent the way this page expects. Refresh the page and try again.",
        )
      })

      it('keeps a file of the wrong type at 400 with its own sentence', async () => {
        const f = new FormData()
        f.append('files', new Blob(['a'], { type: 'image/png' }), 'a.png')
        const r = await send(srv.url, f)
        expect(r.status).to.equal(400)
        expect(r.body.error).to.include({
          code: 'HTTP_BAD_REQUEST',
          message: 'Invalid file type. Allowed types: text/plain, application/octet-stream',
        })
      })

      it('keeps a body cut short at 400 with its plain sentence', async () => {
        const r = await send(srv.url, truncated('files'), { 'content-type': 'multipart/form-data; boundary=XyZ' })
        expect(r.status).to.equal(400)
        expect(r.body.error.message).to.equal("The upload didn't complete. Check your connection and try again.")
      })

      it('still takes a good upload', async () => {
        const r = await send(srv.url, form([['files', 10]]))
        expect(r.status).to.equal(200)
      })
    })

    describe('partial mode', () => {
      let srv: Server
      before(async () => {
        srv = await serveService({ ...base, partialUpload: true, maxRequestBytes: 100 })
      })
      after(() => srv.close())

      it('answers a batch over the total limit with 413 and its plain sentence', async () => {
        const r = await send(srv.url, form([['files', 60], ['files', 60]]))
        expect(r.status).to.equal(413)
        expect(r.body.error.message).to.match(/^This upload is larger than the \d+ MB total limit\. Upload fewer files at a time\.$/)
      })
    })
  })
})
