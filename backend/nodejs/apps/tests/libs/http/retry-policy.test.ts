import { expect } from 'chai'
import fs from 'node:fs'
import path from 'node:path'
import http from 'node:http'
import { AddressInfo } from 'node:net'
import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios'
import axiosRetry from 'axios-retry'
import { RETRY_POLICY } from '../../../src/libs/http/retry-policy'

type Answer = number | 'reset' | ((req: http.IncomingMessage) => void)

describe('RETRY_POLICY (the default axios instance retries)', () => {
  let server: http.Server
  let base: string
  let answers: Answer[]
  let received: string[]

  before(async () => {
    server = http.createServer((req, res) => {
      received.push(req.method ?? '')
      // The last answer repeats, so a retried call keeps getting it.
      const answer = answers.length > 1 ? answers.shift()! : answers[0]!
      req.resume()
      req.on('end', () => {
        if (answer === 'reset') req.socket.destroy()
        else if (typeof answer === 'function') answer(req)
        else res.writeHead(answer, { 'retry-after': '0' }).end('{}')
      })
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  after(() => new Promise<void>((resolve) => server.close(() => resolve())))

  beforeEach(() => {
    received = []
  })

  const client = (): AxiosInstance => {
    const instance = axios.create()
    axiosRetry(instance, { ...RETRY_POLICY, retryDelay: () => 0 })
    return instance
  }

  const send = async (method: string, ...replies: Answer[]): Promise<unknown> => {
    answers = replies
    return client()
      .request({ method, url: `${base}/x`, data: method === 'get' || method === 'head' ? undefined : {} })
      .then((r) => r.status, (e: AxiosError) => e.response?.status ?? e.code)
  }

  describe('a write the upstream may already have acted on is sent once', () => {
    for (const [method, answer] of [
      ['post', 503],
      ['post', 'reset'],
      ['patch', 500],
      ['post', 429],
      ['put', 502],
      ['put', 'reset'],
      ['delete', 503],
      ['delete', 'reset'],
    ] as const) {
      it(`${method.toUpperCase()} answered ${answer}`, async () => {
        await send(method, answer)
        expect(received).to.have.length(1)
      })
    }
  })

  describe('a read is retried three times', () => {
    for (const [method, answer] of [
      ['get', 503],
      ['get', 'reset'],
      ['head', 503],
      ['get', 429],
    ] as const) {
      it(`${method.toUpperCase()} answered ${answer}`, async () => {
        await send(method, answer)
        expect(received).to.have.length(4)
      })
    }

    it('and recovers when a retry is answered', async () => {
      expect(await send('get', 503, 503, 200)).to.equal(200)
      expect(received).to.have.length(3)
    })
  })

  describe('which failures of a read are retried', () => {
    const failure = (status?: number, headers: Record<string, string> = {}, code?: string): AxiosError => {
      const config = { method: 'get', headers: {} } as InternalAxiosRequestConfig
      const response =
        status === undefined ? undefined : { status, statusText: '', headers, config, data: {} }
      return new AxiosError('failed', code, config, {}, response)
    }
    const retried = (error: AxiosError): boolean => RETRY_POLICY.retryCondition!(error) as boolean

    it('a 429 with no Retry-After, or one of 5 s at most', () => {
      expect(retried(failure(429))).to.equal(true)
      expect(retried(failure(429, { 'retry-after': '5' }))).to.equal(true)
    })

    it('not a 429 whose Retry-After would hold the request open', () => {
      expect(retried(failure(429, { 'retry-after': '2351' }))).to.equal(false)
    })

    it('not a 4xx', () => {
      expect(retried(failure(404))).to.equal(false)
    })

    it('not a timeout', () => {
      expect(retried(failure(undefined, {}, 'ECONNABORTED'))).to.equal(false)
    })
  })

  it('does not retry a call we cancelled', async () => {
    const aborter = new AbortController()
    const instance = client()
    let attempts = 0
    instance.interceptors.request.use((config) => {
      attempts += 1
      return config
    })
    answers = [() => aborter.abort()]

    const error = await instance.get(`${base}/x`, { signal: aborter.signal }).catch((e: unknown) => e)

    expect(axios.isCancel(error)).to.equal(true)
    expect(attempts).to.equal(1)
  })

  it('waits with exponential backoff, three times', () => {
    expect(RETRY_POLICY.retryDelay).to.equal(axiosRetry.exponentialDelay)
    expect(RETRY_POLICY.retries).to.equal(3)
  })

  // A route module that installed retries on import changed every caller's requests (POSTs sent four times).
  it('is installed on the default instance by index.ts, and no other src file can install retries', () => {
    const src = path.join(__dirname, '..', '..', '..', 'src')
    const read = (rel: string): string => fs.readFileSync(path.join(src, rel), 'utf8')
    // Installing needs axios-retry's default export, under whatever name; a named helper such as retryAfter cannot install.
    const takesDefault =
      /^\s*import\s+(?!type\b)(?:[\w$]+|\*\s*as\s+[\w$]+)\s*(?:,\s*\{[^}]*\})?\s*from\s*['"]axios-retry['"]|require\(\s*['"]axios-retry['"]\s*\)/m
    const importers: string[] = []
    const walk = (dir: string): void => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full)
        else if (entry.name.endsWith('.ts') && takesDefault.test(fs.readFileSync(full, 'utf8'))) {
          importers.push(path.relative(src, full).split(path.sep).join('/'))
        }
      }
    }
    walk(src)

    expect(importers.sort()).to.deep.equal(['index.ts', 'libs/http/retry-policy.ts'])
    expect(read('index.ts')).to.match(/^axiosRetry\(axios, RETRY_POLICY\);$/m)
    expect(read('libs/http/retry-policy.ts')).to.not.match(/^(?!\s*(?:\/\/|\*)).*\baxiosRetry\(/m)
  })
})
