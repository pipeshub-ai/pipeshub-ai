import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import mongoose from 'mongoose'
import {
  STAND_IN_TOKEN_TTL_SECONDS,
  hydrateScopedRequestAsUser,
} from '../../../../src/modules/enterprise_search/utils/scoped-request'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'

describe('hydrateScopedRequestAsUser', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('gives the token it mints the role claim Node requires of a session, as member', async () => {
    const orgId = new mongoose.Types.ObjectId()
    sinon.stub(Org, 'find').returns({
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().returnsThis(),
      exec: sinon.stub().resolves([{ _id: orgId }]),
    } as any)
    sinon.stub(Users, 'findOne').resolves({
      _id: new mongoose.Types.ObjectId(),
      orgId,
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

  it('looks the user up in the org a token names, without guessing the org', async () => {
    const orgId = new mongoose.Types.ObjectId().toString()
    const orgFind = sinon.stub(Org, 'find')
    const findOne = sinon.stub(Users, 'findOne').resolves({ _id: new mongoose.Types.ObjectId(), orgId, email: 'person@example.com' } as any)
    const req: any = { headers: {}, params: {}, tokenPayload: { email: 'person@example.com', orgId } }

    await hydrateScopedRequestAsUser(req, { jwtSecret: 'session-secret', scopedJwtSecret: 'scoped-secret' } as any)

    expect(orgFind.called).to.equal(false)
    expect(findOne.firstCall.args[0]).to.deep.include({ email: 'person@example.com', orgId })
    expect(String(req.user.orgId)).to.equal(orgId)
  })
})
