import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import sinon from 'sinon'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import {
  listKnowledgeBasesSchema,
  uploadRecordsSchema,
} from '../../../src/modules/knowledge_base/validators/validators'
import {
  listProjectsQuerySchema,
  upsertProjectMembersSchema,
} from '../../../src/modules/projects/validators/project.validators'
import { enterpriseSearchSearchSchema } from '../../../src/modules/enterprise_search/validators/es_validators'
import { LogRecord, captureLogRecords, newRequestId, recordsOf } from '../../helpers/log-capture'

// A caller's mistake that a schema callback answers with a thrown non-zod error (a plain Error, or a TypeError
// from reading a field of null) is a 400 VALIDATION_ERROR, logged at WARNING `bad_request` with no stack, and the
// 400 body does not echo the value sent.

const KB_ID = '0f8fad5b-d9cb-469f-a165-70867728950e'
const PROJECT_ID = '507f1f77bcf86cd799439011'
const MAYBE = 'n5m-maybe'
const LONG_SEARCH = `n5m${'q'.repeat(1000)}`

describe('ValidationMiddleware: a schema callback that throws on caller input is a 400, not a 500', () => {
  let server: http.Server
  let port = 0
  let records: LogRecord[]
  let handled = 0

  before(async () => {
    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    const done = (_req: express.Request, res: express.Response) => {
      handled += 1
      res.json({})
    }
    // The real schemas, on the paths app.ts mounts them at (auth and the multipart step left out).
    app.post('/api/v1/knowledgeBase/:kbId/upload', ValidationMiddleware.validate(uploadRecordsSchema), done)
    app.get('/api/v1/knowledgeBase', ValidationMiddleware.validate(listKnowledgeBasesSchema), done)
    app.put('/api/v1/projects/:projectId/members', ValidationMiddleware.validate(upsertProjectMembersSchema), done)
    app.get('/api/v1/projects', ValidationMiddleware.validate(listProjectsQuerySchema), done)
    app.post('/api/v1/search', ValidationMiddleware.validate(enterpriseSearchSearchSchema), done)
    app.use(ErrorMiddleware.handleError())
    server = http.createServer((req, res) => void app(req, res))
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    port = (server.address() as AddressInfo).port
  })

  after(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  beforeEach(() => {
    records = captureLogRecords()
    handled = 0
  })

  afterEach(() => {
    sinon.restore()
  })

  /** Sends the request; returns the answer and the request's own lines once its access line is in. */
  const send = async (method: 'GET' | 'POST' | 'PUT', path: string, body?: unknown) => {
    const requestId = newRequestId('n5m')
    const payload = body === undefined ? '' : JSON.stringify(body)
    const answer = await new Promise<{ status: number; text: string }>((resolve, reject) => {
      const req = http.request(
        {
          host: '127.0.0.1',
          port,
          path,
          method,
          headers: {
            'content-type': 'application/json',
            'content-length': Buffer.byteLength(payload),
            'x-request-id': requestId,
          },
        },
        (res) => {
          let text = ''
          res.setEncoding('utf8')
          res.on('data', (chunk: string) => (text += chunk))
          res.on('end', () => resolve({ status: res.statusCode ?? 0, text }))
        },
      )
      req.on('error', reject)
      req.end(payload)
    })
    const deadline = Date.now() + 5000
    while (Date.now() < deadline && !recordsOf(records, requestId).some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setImmediate(resolve))
    }
    return { ...answer, requestId, lines: recordsOf(records, requestId) }
  }

  /** The 400 shape every case must have: the field and rule, WARNING bad_request, no ERROR line, no stack. */
  const expectRefused = (r: Awaited<ReturnType<typeof send>>, field: string) => {
    expect(r.status, r.text).to.equal(400)
    expect(handled).to.equal(0)
    const error = JSON.parse(r.text).error
    expect(error.code).to.equal('VALIDATION_ERROR')
    const failure = r.lines.find((l) => l.message === 'http.request.error')
    expect(failure, 'http.request.error').to.not.equal(undefined)
    expect(failure).to.include({ level: 'warning', status: 400, error_class: 'bad_request' })
    const fields = (failure!.metadata.error.metadata.errors as Array<{ field: string }>).map((e) => e.field)
    expect(fields).to.deep.equal([field])
    expect(r.lines.map((l) => `${l.level} ${l.message}`)).to.deep.equal([
      'warning http.request.error',
      'info http.request.end',
    ])
    expect(JSON.stringify(r.lines)).to.not.match(/"stack"/)
  }

  it('isVersioned "maybe" on an upload is a 400 naming body.isVersioned, without the value', async () => {
    const r = await send('POST', `/api/v1/knowledgeBase/${KB_ID}/upload`, { isVersioned: MAYBE })

    expectRefused(r, 'body.isVersioned')
    expect(r.text).to.not.include(MAYBE)
  })

  it('a knowledge base search over 1000 characters is a 400 naming query.search, without the value', async () => {
    const r = await send('GET', `/api/v1/knowledgeBase?search=${LONG_SEARCH}`)

    expectRefused(r, 'query.search')
    expect(r.text).to.not.include(LONG_SEARCH)
    expect(JSON.stringify(r.lines)).to.not.include(LONG_SEARCH)
  })

  it('a null member row is a 400 naming body.members.0', async () => {
    const r = await send('PUT', `/api/v1/projects/${PROJECT_ID}/members`, { members: [null] })

    expectRefused(r, 'body.members.0')
  })

  // Control: holds on main and must keep holding.
  it('a valid isVersioned string and a 1000-character search still pass', async () => {
    const upload = await send('POST', `/api/v1/knowledgeBase/${KB_ID}/upload`, { isVersioned: '0' })
    expect(upload.status, upload.text).to.equal(200)
    const search = await send('GET', `/api/v1/knowledgeBase?search=${'q'.repeat(1000)}`)
    expect(search.status, search.text).to.equal(200)
  })

  // Express 4's extended query parser keeps `valueOf` and `toString` keys, so `?page[valueOf]=1&page[toString]=1`
  // reaches a `Number(arg)` preprocess as an object it cannot convert: a TypeError inside zod. About 30 routes
  // share such preprocesses (route-fuzz-main.txt). No field can be named: the throw carries no path.
  it('a query page that cannot be converted to a number is a 400 (no field), without the value', async () => {
    const r = await send('GET', '/api/v1/projects?page[valueOf]=n5m-v&page[toString]=n5m-t')

    expectRefused(r, '')
    expect(r.text).to.not.include('n5m-v')
    expect(r.text).to.not.include('n5m-t')
  })

  it('a JSON body limit that cannot be converted to a number is a 400 (no field)', async () => {
    const r = await send('POST', '/api/v1/search', { query: 'n5m', limit: { valueOf: 1, toString: 1 } })

    expectRefused(r, '')
  })
})
