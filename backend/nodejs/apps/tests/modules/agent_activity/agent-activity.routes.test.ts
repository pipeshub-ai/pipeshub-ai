import 'reflect-metadata'
import express from 'express'
import type { Server } from 'http'
import { Container } from 'inversify'
import { expect } from 'chai'
import { createAgentActivityRouter } from '../../../src/modules/agent_activity/agent-activity.routes'
import {
  setAgentCallRepository,
  type AgentCallDto,
  type AgentCallFilter,
  type AgentCallRepository,
} from '../../../src/libs/repositories/agent-call-event'
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'
import { setUserRepository } from '../../../src/libs/repositories/user'

const ORG_A = 'a'.repeat(24)
const ORG_B = 'b'.repeat(24)
const ADMIN = '1'.repeat(24)
const MEMBER = '2'.repeat(24)

const row = (orgId: string, n: number): AgentCallDto => ({
  _id: n.toString(16).padStart(24, '0'), orgId, userId: ADMIN, tokenId: `t${n}`, tokenKind: 'pat', method: 'POST',
  route: '/r', statusCode: 200, durationMs: 1, scopeDenied: false, count: 1, createdAt: new Date(1_700_000_000_000 + n * 1000),
})

/** Newest first, one org at a time, honouring the cursor and the limit, as the stores do. */
class FakeRepo implements AgentCallRepository {
  rows = [1, 2, 3, 4, 5].map((n) => row(ORG_A, n)).concat([row(ORG_B, 6)])
  seen: { orgId: string; filter: AgentCallFilter; limit: number }[] = []
  async record(): Promise<void> {}
  async list(orgId: string, filter: AgentCallFilter, limit: number): Promise<AgentCallDto[]> {
    this.seen.push({ orgId, filter, limit })
    return this.rows
      .filter((r) => r.orgId === orgId)
      .filter((r) => !filter.before || r.createdAt < filter.before.createdAt)
      .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime())
      .slice(0, limit)
  }
}

describe('GET /api/v1/agent-activity', () => {
  let server: Server | undefined
  let repo: FakeRepo

  beforeEach(() => {
    repo = new FakeRepo()
    setAgentCallRepository(repo)
    setUserRepository({
      getRoleSnapshot: async (_org: string, userId: string) => ({ role: userId === ADMIN ? 'admin' : 'member' }),
    } as never)
  })
  afterEach(async () => {
    setAgentCallRepository()
    setUserRepository()
    if (server) await new Promise<void>((r) => server!.close(() => r()))
    server = undefined
  })

  async function get(query = '', who: { org: string; user: string; scopes?: string[] } = { org: ORG_A, user: ADMIN }) {
    const container = new Container()
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: any, next: any) => {
        req.user = { orgId: who.org, userId: who.user, ...(who.scopes && { isOAuth: true, oauthScopes: who.scopes }) }
        runWithOrgContext(makeOrgContext({ orgId: who.org, userId: who.user, kind: 'user', source: 'jwt' }), next)
      },
    })
    const app = express()
    app.use('/api/v1/agent-activity', createAgentActivityRouter(container))
    app.use((err: any, _req: any, res: any, _next: any) => res.status(err?.statusCode ?? 500).json({ message: err?.message })) // eslint-disable-line @typescript-eslint/no-unused-vars
    await new Promise<void>((r) => { server = app.listen(0, () => r()) })
    const res = await fetch(`http://127.0.0.1:${(server!.address() as { port: number }).port}/api/v1/agent-activity${query}`)
    return { status: res.status, body: (await res.json()) as any }
  }

  it("shows an admin their own org's rows only, newest first", async () => {
    const { status, body } = await get()
    expect(status).to.equal(200)
    expect(body.items.map((i: any) => i.tokenId)).to.deep.equal(['t5', 't4', 't3', 't2', 't1'])
    expect(JSON.stringify(body)).to.not.contain(ORG_B)
    expect(body.items[0]).to.not.have.property('orgId')
  })

  it("ignores an org named in the request: org A's admin cannot read org B", async () => {
    await get(`?orgId=${ORG_B}`)
    expect(repo.seen[0]!.orgId).to.equal(ORG_A)
  })

  it('refuses a member', async () => {
    const { status } = await get('', { org: ORG_A, user: MEMBER })
    expect(status).to.equal(403)
    expect(repo.seen).to.have.length(0)
  })

  it('refuses a token without the org admin scope, and accepts one with it', async () => {
    expect((await get('', { org: ORG_A, user: ADMIN, scopes: ['kb:read'] })).status).to.equal(403)
    expect((await get('', { org: ORG_A, user: ADMIN, scopes: ['org:admin'] })).status).to.equal(200)
  })

  it('pages with a cursor and omits it on the last page', async () => {
    const first = await get('?limit=2')
    expect(first.body.items.map((i: any) => i.tokenId)).to.deep.equal(['t5', 't4'])
    const second = await get(`?limit=2&cursor=${first.body.nextCursor}`)
    expect(second.body.items.map((i: any) => i.tokenId)).to.deep.equal(['t3', 't2'])
    const last = await get(`?limit=2&cursor=${second.body.nextCursor}`)
    expect(last.body.items.map((i: any) => i.tokenId)).to.deep.equal(['t1'])
    expect(last.body).to.not.have.property('nextCursor')
  })

  it('caps the limit at 200 and passes the filters on', async () => {
    await get(
      `?limit=9999&from=2026-01-01T00:00:00Z&to=2026-02-01T00:00:00Z&userId=${ADMIN}&tokenId=abc&tokenKind=oauth&client=curl&status=4xx`,
    )
    const { filter, limit } = repo.seen[0]!
    expect(limit).to.equal(201) // one more than the page, to know whether there is a next one
    expect(filter).to.deep.equal({
      from: new Date('2026-01-01T00:00:00Z'), to: new Date('2026-02-01T00:00:00Z'), userId: ADMIN, tokenId: 'abc',
      tokenKind: 'oauth', client: 'curl', statusClass: '4xx',
    })
  })

  it('rejects malformed filters and cursors', async () => {
    for (const q of ['?status=3xx', '?tokenKind=x', '?userId=nope', '?from=yesterday', '?cursor=@@@', '?limit=abc']) {
      expect((await get(q)).status, q).to.equal(400)
    }
    expect(repo.seen).to.have.length(0)
  })
})
