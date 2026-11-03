import 'reflect-metadata'
import express from 'express'
import type { Server } from 'http'
import { Container } from 'inversify'
import { expect } from 'chai'
import { createDelegationRouter } from '../../../../src/modules/oauth_provider/routes/delegation.routes'

/**
 * The Community half of the delegation router.
 *
 * These tests are about an absence, which is both the awkward thing to test
 * and the thing most worth testing. The whole edition boundary rests on there
 * being no way to write an allow-list entry in this build, and a route
 * quietly reappearing here would fail nothing else in the suite.
 */
describe('Delegation routes (Community edition)', () => {
  let server: Server | undefined

  afterEach(async () => {
    if (server) {
      await new Promise<void>((resolve) => server!.close(() => resolve()))
      server = undefined
    }
  })

  async function listen(): Promise<number> {
    const app = express()
    app.use(express.json())
    app.use(
      '/api/v1/service-account-delegations',
      createDelegationRouter(new Container()),
    )
    // Close enough to the app's error middleware to read back the status and
    // message the router hands on.
    app.use((err: any, _req: any, res: any, _next: any) => {
      res
        .status(err?.statusCode ?? 500)
        .json({ code: err?.code, message: err?.message })
    })
    return new Promise((resolve) => {
      server = app.listen(0, () => {
        resolve((server!.address() as { port: number }).port)
      })
    })
  }

  const BASE = '/api/v1/service-account-delegations'

  it('needs nothing from the container', () => {
    // The Enterprise router resolves a controller, a logger and the app
    // config. The stub must not, or a Community boot would fail on a binding
    // that is deliberately absent.
    expect(() => createDelegationRouter(new Container())).to.not.throw()
  })

  it('answers the capability probe instead of 404ing it', async () => {
    const port = await listen()
    const res = await fetch(`http://127.0.0.1:${port}${BASE}/capability`)
    const body = await res.json()

    expect(res.status).to.equal(200)
    expect(body.available).to.equal(false)
    expect(body.capability).to.equal('service-account-delegation')
  })

  it('refuses to list delegations, and says why', async () => {
    const port = await listen()
    const res = await fetch(`http://127.0.0.1:${port}${BASE}?serviceAccountId=x`)
    const body = await res.json()

    // 501 rather than 404: a missing route reads as a broken deployment,
    // which is the complaint this is meant to avoid.
    expect(res.status).to.equal(501)
    expect(body.message).to.contain('Enterprise')
  })

  it('refuses to grant a delegation', async () => {
    const port = await listen()
    const res = await fetch(`http://127.0.0.1:${port}${BASE}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ serviceAccountId: 'a', subject: 'x@example.com' }),
    })

    expect(res.status).to.equal(501)
  })

  it('refuses to revoke a delegation', async () => {
    const port = await listen()
    const res = await fetch(`http://127.0.0.1:${port}${BASE}/someone`, {
      method: 'DELETE',
    })

    expect(res.status).to.equal(501)
  })

  it('refuses to read the delegation event log', async () => {
    const port = await listen()
    const res = await fetch(`http://127.0.0.1:${port}${BASE}/events`)

    expect(res.status).to.equal(501)
  })
})
