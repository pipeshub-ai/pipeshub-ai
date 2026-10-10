import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import { Container } from 'inversify'
import http from 'http'
import { AddressInfo } from 'net'
import { createAgentConversationalRouter } from '../../../../src/modules/enterprise_search/routes/es.routes'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { markingGuards } from '../helpers/guarded-chat'
import { turnDeps } from '../helpers/turn-deps'
import { bindCollaborationStubs } from '../helpers/collaboration-world'

describe('GET /agents/handle-availability rate limit', () => {
  let server: ReturnType<express.Express['listen']>
  let upstream: http.Server
  let base: string

  before(async () => {
    upstream = http.createServer((_req, res) => { res.setHeader('content-type', 'application/json'); res.end('{"available":true}') })
    await new Promise<void>((r) => upstream.listen(0, '127.0.0.1', r))
    const passthrough = () => sinon.stub().callsFake((_req, _res, next) => next())
    const c = new Container()
    c.bind('AuthMiddleware').toConstantValue({ authenticate: passthrough(), scopedTokenValidator: () => passthrough() })
    c.bind('AppConfig').toConstantValue({ aiBackend: `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`, jwtSecret: 'j', scopedJwtSecret: 's' })
    c.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(markingGuards())
    c.bind(COLLAB_TYPES.ConversationTurnDeps).toConstantValue(turnDeps())
    bindCollaborationStubs(c)
    const app = express()
    app.use((req, _res, next) => {
      ;(req as any).user = { userId: 'u-limit', orgId: 'o-1' }
      ;(req as any).tokenPayload = { scopes: ['agent:read'] }
      next()
    })
    app.use('/agents', createAgentConversationalRouter(c))
    server = app.listen(0)
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })
  after(() => { server.close(); upstream.close() })

  it('answers 429 RATE_LIMITED on the 61st request in a minute', async () => {
    const statuses: number[] = []
    for (let i = 0; i < 61; i += 1) {
      const res = await fetch(`${base}/agents/handle-availability?handle=x`)
      statuses.push(res.status)
      if (i === 60) {
        const body = await res.json() as any
        expect(body.error.code).to.equal('RATE_LIMITED')
        expect(body.error.details).to.have.property('retryAfter')
      }
    }
    expect(statuses.slice(0, 60).every((s) => s !== 429)).to.equal(true)
    expect(statuses[60]).to.equal(429)
  }).timeout(15000)
})
