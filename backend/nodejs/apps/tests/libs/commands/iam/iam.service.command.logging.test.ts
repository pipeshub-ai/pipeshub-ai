import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import http from 'http'
import { AddressInfo } from 'net'
import { Logger } from '../../../../src/libs/services/logger.service'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'

type CommandModule = typeof import('../../../../src/libs/commands/iam/iam.service.command')
const MODULE_SUFFIX = 'libs/commands/iam/iam.service.command.ts'

// The command takes its logger once, at load, so load a private copy that
// logs into the recorder, then put the cached module back.
const privateCommandModule = (recorder: Logger): CommandModule => {
  const cached = Object.keys(require.cache).filter((key) => key.endsWith(MODULE_SUFFIX))
  const originals = cached.map((key) => [key, require.cache[key]] as const)
  for (const key of cached) delete require.cache[key]
  const getInstance = sinon.stub(Logger, 'getInstance').returns(recorder)
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require('../../../../src/libs/commands/iam/iam.service.command') as CommandModule
  } finally {
    getInstance.restore()
    for (const key of Object.keys(require.cache)) {
      if (key.endsWith(MODULE_SUFFIX)) delete require.cache[key]
    }
    for (const [key, mod] of originals) require.cache[key] = mod
  }
}

const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJ1c2VySWQiOiJ1MSJ9.c2lnbmF0dXJlLXZhbHVl'

describe('IAMServiceCommand logging', () => {
  let server: http.Server
  let origin: string
  let logged: Array<{ label: string; text: string }>
  let command: CommandModule

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
    command = privateCommandModule({
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
    new command.IAMServiceCommand({
      uri: `${origin}${path}`,
      method: HttpMethod.POST,
      headers: { authorization: `Bearer ${TOKEN}` },
      body: { password: 'hunter2-do-not-log' },
    }).execute()

  it('logs a successful call without the token, the body or a query token', async () => {
    await run('/api/v1/users?token=query-token-value&page=1')

    expect(logged.map((e) => e.label)).to.include('debug: IAM service command response')
    expect(leaking(TOKEN, 'hunter2-do-not-log', 'query-token-value')).to.deep.equal([])
    expect(logged.find((e) => e.label === 'debug: IAM service command response')?.text).to.include('page=1')
  })

  it('logs a failed call without the token, the body or a query token', async () => {
    let failure: unknown
    try {
      await run('/drop?access_token=query-token-value')
    } catch (error) {
      failure = error
    }

    expect(failure).to.be.instanceOf(Error)
    expect(logged.map((e) => e.label)).to.include('error: IAM service command failed')
    expect(leaking(TOKEN, 'hunter2-do-not-log', 'query-token-value')).to.deep.equal([])
  })
})
