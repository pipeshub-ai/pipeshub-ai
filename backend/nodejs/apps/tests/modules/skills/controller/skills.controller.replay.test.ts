/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import http from 'node:http'
import { AddressInfo } from 'node:net'
import axios from 'axios'
import axiosRetry from 'axios-retry'
import { RETRY_POLICY } from '../../../../src/libs/http/retry-policy'
import { ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'
import {
  createSkill,
  deleteSkill,
  listSkills,
  patchSkillBody,
  removeSkillResource,
  updateSkill,
} from '../../../../src/modules/skills/controller/skills.controller'

type Handler = ReturnType<typeof listSkills>

// The skills service commits a write, then the connection drops before its answer reaches the proxy. A replay of that
// write would add a second revision, or answer 404 for a delete that worked.
describe('skills proxy: a write whose answer was lost is not sent again', () => {
  let server: http.Server
  let appConfig: Parameters<typeof listSkills>[0]
  let interceptors: { requestInterceptorId: number; responseInterceptorId: number }
  let received: string[]
  let skill: { exists: boolean; revisions: number; resources: Set<string> }
  let dropNext: boolean

  // As the Python router does: an update snapshots a revision; a delete of something absent is 404.
  const handle = (method: string, route: string): [number, unknown] => {
    if (route === '/api/v1/skills/foo' && method === 'PUT') {
      skill.revisions += 1
      return [200, { name: 'foo' }]
    }
    if (route === '/api/v1/skills/foo' && method === 'DELETE') {
      if (!skill.exists) return [404, { detail: "Skill 'foo' not found." }]
      skill.exists = false
      return [200, { status: 'success' }]
    }
    if (route === '/api/v1/skills/foo/resource' && method === 'DELETE') {
      return skill.resources.delete('a.md') ? [200, { status: 'success' }] : [404, { detail: 'not found' }]
    }
    return [200, { skills: [] }]
  }

  before(async () => {
    server = http.createServer((req, res) => {
      const route = (req.url ?? '').split('?')[0]!
      received.push(`${req.method} ${route}`)
      req.resume()
      req.on('end', () => {
        const [status, body] = handle(req.method ?? '', route)
        if (dropNext) {
          dropNext = false
          req.socket.destroy()
          return
        }
        res.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body))
      })
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    appConfig = { aiBackend: `http://127.0.0.1:${(server.address() as AddressInfo).port}` } as typeof appConfig
    // As index.ts installs it on the default instance, without the backoff wait.
    interceptors = axiosRetry(axios, { ...RETRY_POLICY, retryDelay: () => 0 })
  })

  after(async () => {
    axios.interceptors.request.eject(interceptors.requestInterceptorId)
    axios.interceptors.response.eject(interceptors.responseInterceptorId)
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  beforeEach(() => {
    received = []
    skill = { exists: true, revisions: 0, resources: new Set(['a.md']) }
    dropNext = true
  })

  const call = async (handler: Handler, req: Record<string, unknown> = {}) => {
    const out: { status?: number; error?: unknown } = {}
    const res = {
      status: (s: number) => ((out.status = s), res),
      json: () => res,
    }
    await handler(appConfig)(
      { headers: { authorization: 'Bearer t' }, body: { content: 'x' }, query: {}, params: { name: 'foo' }, ...req } as never,
      res as never,
      (e: unknown) => (out.error = e),
    )
    return out
  }

  it('updateSkill (PUT): one request, one revision, and the client is told the service was unavailable', async () => {
    const out = await call(updateSkill)

    expect(received).to.deep.equal(['PUT /api/v1/skills/foo'])
    expect(skill.revisions).to.equal(1)
    expect(out.error).to.be.instanceOf(ServiceUnavailableError)
    expect(out.status).to.equal(undefined)
  })

  it('deleteSkill (DELETE): one request, and never a 404 for the delete that worked', async () => {
    const out = await call(deleteSkill)

    expect(received).to.deep.equal(['DELETE /api/v1/skills/foo'])
    expect(skill.exists).to.equal(false)
    expect(out.error).to.be.instanceOf(ServiceUnavailableError)
    expect(out.status).to.equal(undefined)
  })

  it('removeSkillResource (DELETE): one request, and never a 404 for the removal that worked', async () => {
    const out = await call(removeSkillResource, { query: { path: 'a.md' } })

    expect(received).to.deep.equal(['DELETE /api/v1/skills/foo/resource'])
    expect(out.error).to.be.instanceOf(ServiceUnavailableError)
    expect(out.status).to.equal(undefined)
  })

  it('patchSkillBody (PATCH) and createSkill (POST): one request each', async () => {
    await call(patchSkillBody, { body: { old_string: 'a', new_string: 'b' } })
    dropNext = true
    await call(createSkill)

    expect(received).to.deep.equal(['PATCH /api/v1/skills/foo/body', 'POST /api/v1/skills/'])
  })

  it('listSkills (GET): retried after the dropped connection, and answered', async () => {
    const out = await call(listSkills)

    expect(received).to.deep.equal(['GET /api/v1/skills/', 'GET /api/v1/skills/'])
    expect(out.status).to.equal(200)
  })
})
