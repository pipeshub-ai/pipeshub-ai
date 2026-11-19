import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import http from 'http'
import { AddressInfo } from 'net'
import { Logger } from '../../../../src/libs/services/logger.service'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'
import { IAMServiceCommand } from '../../../../src/libs/commands/iam/iam.service.command'

const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJ1c2VySWQiOiJ1MSJ9.c2lnbmF0dXJlLXZhbHVl'

describe('IAMServiceCommand logging', () => {
  let server: http.Server
  let origin: string
  let logged: Array<{ label: string; text: string }>

  const leaking = (...secrets: string[]): string[] =>
    logged.filter((entry) => secrets.some((s) => entry.text.includes(s))).map((entry) => entry.label)

  before(async () => {
    server = http.createServer((req, res) => {
      if ((req.url ?? '').startsWith('/drop')) {
        req.socket.destroy()
        return
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end('{"ok":true}')
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  after(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  beforeEach(() => {
    logged = []
    const record =
      (level: string) =>
      (message: string, meta?: unknown): void => {
        logged.push({ label: `${level}: ${message}`, text: `${message} ${JSON.stringify(meta)}` })
      }
    // The call line takes its logger when it is written.
    sinon.stub(Logger, 'getInstance').returns({
      error: record('error'),
      warn: record('warn'),
      info: record('info'),
      debug: record('debug'),
    } as unknown as Logger)
  })

  afterEach(() => {
    sinon.restore()
  })

  const run = (path: string): Promise<unknown> =>
    new IAMServiceCommand({
      uri: `${origin}${path}`,
      method: HttpMethod.POST,
      headers: { authorization: `Bearer ${TOKEN}` },
      body: { password: 'hunter2-do-not-log' },
    }).execute()

  it('logs a successful call as one call line, without the token, the body or the query', async () => {
    await run('/api/v1/users?token=query-token-value&page=1')

    expect(logged.map((e) => e.label)).to.deep.equal(['debug: upstream.call.end'])
    expect(leaking(TOKEN, 'hunter2-do-not-log', 'query-token-value', 'page=1')).to.deep.equal([])
  })

  it('logs a failed call once per attempt, without the token, the body or the query', async () => {
    let failure: unknown
    try {
      await run('/drop?access_token=query-token-value')
    } catch (error) {
      failure = error
    }

    expect(failure).to.be.instanceOf(Error)
    // One line per attempt, at WARN: an outage of another service is not our bug.
    expect(logged.map((e) => e.label)).to.deep.equal(Array(3).fill('warn: upstream.call.end'))
    expect(leaking(TOKEN, 'hunter2-do-not-log', 'query-token-value')).to.deep.equal([])
  })
})
