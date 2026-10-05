import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import mongoose from 'mongoose'
import {
  STAND_IN_TOKEN_TTL_SECONDS,
  hydrateScopedRequestAsUser,
} from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'

describe('hydrateScopedRequestAsUser', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('gives the token it mints the role claim Node requires of a session, as member', async () => {
    sinon.stub(Users, 'findOne').resolves({
      _id: new mongoose.Types.ObjectId(),
      orgId: new mongoose.Types.ObjectId(),
      email: 'person@example.com',
      fullName: 'Person',
      role: 'admin',
    } as any)
    const req: any = {
      headers: { authorization: 'Bearer slack-service-token' },
      params: {},
      tokenPayload: { email: 'person@example.com' },
    }

    await hydrateScopedRequestAsUser(req, {
      jwtSecret: 'session-secret',
      scopedJwtSecret: 'scoped-secret',
    } as any)

    const token = req.headers.authorization.replace('Bearer ', '')
    const claims = jwt.verify(token, 'session-secret') as Record<string, unknown>
    expect(claims.role).to.equal('member')
    expect(claims.email).to.equal('person@example.com')
    // Node honours it like a session, so it must not outlive the request.
    expect((claims.exp as number) - (claims.iat as number)).to.equal(STAND_IN_TOKEN_TTL_SECONDS)
    expect(STAND_IN_TOKEN_TTL_SECONDS).to.be.at.most(60)
  })
})
