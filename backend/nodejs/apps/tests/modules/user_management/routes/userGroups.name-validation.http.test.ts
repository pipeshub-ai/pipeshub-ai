import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createUserGroupRouter } from '../../../../src/modules/user_management/routes/userGroups.routes'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'

// Group names follow the same rule as team names (1 to 100 characters, trimmed),
// and a rejected name comes back as a field error the form can mark.
describe('Group name validation over HTTP', () => {
  let server: http.Server
  let base: string
  let createUserGroup: sinon.SinonStub

  before(async () => {
    createUserGroup = sinon.stub().callsFake(async (req: any, res: any) => {
      res.status(201).json({ name: req.body.name })
    })
    const container = new Container()
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: any, next: any) => {
        req.user = { userId: 'u1', orgId: 'o1' }
        req.tokenPayload = { isOAuth: false }
        next()
      },
    })
    container.bind('UserGroupController').toConstantValue({ createUserGroup })
    const app = express()
    app.use(express.json())
    app.use('/groups', createUserGroupRouter(container))
    app.use(ErrorMiddleware.handleError())
    // eslint-disable-next-line @typescript-eslint/no-misused-promises -- Express reports its own errors; http only needs the listener
    server = http.createServer(app)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  after(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  const post = async (body: unknown) => {
    const res = await fetch(`${base}/groups`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    return { status: res.status, json: (await res.json()) as any }
  }

  it('rejects a name longer than 100 characters and names the field', async () => {
    const { status, json } = await post({ type: 'custom', name: 'a'.repeat(101) })
    expect(status).to.equal(400)
    expect(json.error.fieldErrors).to.deep.equal([
      { field: 'name', message: 'Name must be at most 100 characters.', code: 'TOO_BIG' },
    ])
    expect(createUserGroup.called).to.equal(false)
  })

  it('rejects a blank name, trimming it first', async () => {
    const { status, json } = await post({ type: 'custom', name: '   ' })
    expect(status).to.equal(400)
    expect(json.error.fieldErrors[0].field).to.equal('name')
  })
})
