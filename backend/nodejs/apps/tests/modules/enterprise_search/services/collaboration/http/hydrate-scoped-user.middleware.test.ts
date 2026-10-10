import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { hydrateScopedUser } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/hydrate-scoped-user.middleware'
import { Users } from '../../../../../../src/modules/user_management/schema/users.schema'

const appConfig = { jwtSecret: 'j', scopedJwtSecret: 's' } as any

describe('hydrateScopedUser middleware', () => {
  afterEach(() => sinon.restore())

  it('is a no-op when the request already has a user (idempotent with the handler-side call)', async () => {
    const find = sinon.stub(Users, 'findOne')
    const req: any = { user: { userId: 'u', orgId: 'o' }, headers: {}, params: {} }
    const err = await new Promise((resolve) => void hydrateScopedUser(appConfig)(req, {} as any, resolve as any))
    expect(err).to.equal(undefined)
    expect(find.called).to.equal(false)
  })

  it('forwards a rejected scoped token to next(err) before any guard runs', async () => {
    const req: any = { tokenPayload: {}, headers: {}, params: {} }
    const err: any = await new Promise((resolve) => void hydrateScopedUser(appConfig)(req, {} as any, resolve as any))
    expect(err.statusCode).to.equal(401)
    expect(req.user).to.equal(undefined)
  })
})
