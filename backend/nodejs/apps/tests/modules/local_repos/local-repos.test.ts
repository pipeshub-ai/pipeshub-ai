import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import * as connectorUtils from '../../../src/modules/tokens_manager/utils/connector.utils'
import { createLocalReposRouter } from '../../../src/modules/local_repos/routes/local_repos.routes'
import {
  commitLocalRepo,
  getLocalRepo,
  registerLocalRepo,
  uploadLocalRepoFiles,
} from '../../../src/modules/local_repos/controller/local_repos.controller'

const SPEC = join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')
const ROUTES = join(__dirname, '..', '..', '..', 'src', 'modules', 'local_repos', 'routes', 'local_repos.routes.ts')
const HTTP_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete'])

type Spec = { paths: Record<string, Record<string, { security?: { oauth2?: string[] }[] }>> }

function servedRoutes(): string[] {
  const container: any = {
    get: sinon.stub().callsFake((key: string) =>
      key === 'AuthMiddleware'
        ? { authenticate: (_req: unknown, _res: unknown, next: () => void) => next() }
        : { connectorBackend: 'http://localhost:8088' },
    ),
  }
  const router: any = createLocalReposRouter(container)
  return router.stack
    .filter((layer: any) => layer.route)
    .flatMap((layer: any) =>
      Object.keys(layer.route.methods).map(
        (method) => `${method} /local-repos${String(layer.route.path).replace(/:(\w+)/g, '{$1}').replace(/\/$/, '')}`,
      ),
    )
    .sort()
}

describe('Local repositories gateway', () => {
  let execute: sinon.SinonStub
  beforeEach(() => {
    execute = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: {} } as never)
    sinon.stub(connectorUtils, 'handleConnectorResponse')
  })
  afterEach(() => sinon.restore())

  it('serves exactly the routes the spec documents, with the scope the router requires', () => {
    const spec = yaml.load(readFileSync(SPEC, 'utf8')) as Spec
    const documented = Object.entries(spec.paths)
      .filter(([path]) => path.startsWith('/local-repos'))
      .flatMap(([path, ops]) => Object.keys(ops).filter((m) => HTTP_METHODS.has(m)).map((m) => `${m} ${path}`))
      .sort()
    expect(documented).to.deep.equal(servedRoutes())
    // reads accept code:read (or code:write); everything else needs code:write
    const source = readFileSync(ROUTES, 'utf8')
    for (const [path, ops] of Object.entries(spec.paths)) {
      if (!path.startsWith('/local-repos')) continue
      for (const [method, op] of Object.entries(ops)) {
        if (!HTTP_METHODS.has(method)) continue
        const scope = op.security?.find((s) => s.oauth2)?.oauth2?.[0]
        expect(scope, `${method} ${path}`).to.equal(method === 'get' ? 'code:read' : 'code:write')
      }
    }
    expect(source).to.include('OAuthScopeNames.CODE_WRITE')
  })

  const call = async (handler: any, req: Record<string, unknown>) => {
    execute.resetHistory()
    await handler({ connectorBackend: 'http://connector:8088' } as never)(
      { user: { userId: 'u1', orgId: 'o1' }, headers: { authorization: 'Bearer t', cookie: 'x=1', 'x-forwarded-user': 'evil' }, ...req } as never,
      {} as never,
      sinon.stub(),
    )
    return execute.firstCall.args as [string, string, Record<string, string>, unknown]
  }

  it('forwards the token, the key and the body to the connectors service and nothing else the client sent', async () => {
    const body = { name: 'shop' }
    const [url, method, headers, sent] = await call(registerLocalRepo, { params: { repoKey: 'repo-key-0001' }, body })
    expect(url).to.equal('http://connector:8088/api/v1/local-repos/repo-key-0001')
    expect(method).to.equal('PUT')
    expect(headers).to.deep.equal({ authorization: 'Bearer t' })
    expect(sent).to.equal(body)
  })

  it('sends no body on a read and posts commit without one', async () => {
    const [url, , , sent] = await call(getLocalRepo, { params: { repoKey: 'repo-key-0001' }, body: { x: 1 } })
    expect(url).to.equal('http://connector:8088/api/v1/local-repos/repo-key-0001')
    expect(sent).to.equal(undefined)
    const [curl] = await call(commitLocalRepo, { params: { repoKey: 'repo-key-0001' } })
    expect(curl).to.equal('http://connector:8088/api/v1/local-repos/repo-key-0001/commit')
  })

  it('passes a refusal of the connectors service on with its status and body, so a quota 413 keeps the list of stored files', async () => {
    const run = async (answer: { statusCode: number; data: unknown }) => {
      execute.resolves(answer as never)
      const json = sinon.stub()
      const status = sinon.stub().returns({ json })
      const next = sinon.stub()
      await uploadLocalRepoFiles({ connectorBackend: 'x' } as never)(
        { user: { userId: 'u1' }, headers: {}, params: { repoKey: 'repo-key-0001' }, body: {} } as never,
        { status } as never,
        next,
      )
      return { status, json, next }
    }
    const quota = { detail: 'past 8 bytes', stored: ['a.py'], rejected: [{ path: 'b.py', reason: 'user_quota' }] }
    const q = await run({ statusCode: 413, data: quota })
    expect(q.status.calledWith(413)).to.equal(true)
    expect(q.json.firstCall.args[0]).to.deep.equal(quota)
    const c = await run({ statusCode: 409, data: { detail: 'You already have 20 repositories' } })
    expect(c.status.calledWith(409)).to.equal(true)
    expect(c.json.firstCall.args[0]).to.deep.equal({ detail: 'You already have 20 repositories' })
    // a success and a server error still take the usual path
    const ok = await run({ statusCode: 200, data: { stored: [] } })
    expect(ok.status.called).to.equal(false)
  })

  it('refuses a request with no signed-in user', async () => {
    const next = sinon.stub()
    await uploadLocalRepoFiles({ connectorBackend: 'x' } as never)({ user: {}, headers: {}, params: { repoKey: 'repo-key-0001' }, body: {} } as never, {} as never, next)
    expect(next.calledOnce).to.equal(true)
    expect(execute.called).to.equal(false)
  })
})
