import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import jwt from 'jsonwebtoken'
import { hydrateScopedRequestAsUser } from '../../../../src/modules/enterprise_search/controller/es_controller'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'

const JWT_SECRET = 'test-jwt-secret'
const appConfig: any = { jwtSecret: JWT_SECRET, scopedJwtSecret: 'test-scoped-secret' }

// T23: the Slack bot's scoped token names a person by email. The same address
// can belong to a member of two organisations; it must resolve only inside the
// organisation the bot belongs to.
describe('T23 Slack scoped-token user resolution is confined to the bot\'s org', () => {
  const EMAIL = 'shared@example.com'
  const orgA = new Types.ObjectId()
  const orgB = new Types.ObjectId()
  const userInA = { _id: new Types.ObjectId(), orgId: orgA, email: EMAIL, fullName: 'In A' }
  const userInB = { _id: new Types.ObjectId(), orgId: orgB, email: EMAIL, fullName: 'In B' }
  const liveOrgs = [{ _id: orgA }, { _id: orgB }]

  let usersFindOne: sinon.SinonStub

  function matches(doc: Record<string, any>, filter: Record<string, any>): boolean {
    return Object.entries(filter).every(([key, value]) => {
      if (key === 'isDeleted') return true
      return String(doc[key]) === String(value)
    })
  }

  beforeEach(() => {
    usersFindOne = sinon.stub(Users, 'findOne').callsFake(((filter: any) =>
      Promise.resolve([userInB, userInA].find((u) => matches(u, filter)) ?? null)) as any)
    sinon.stub(Org, 'findOne').callsFake(((filter: any) =>
      Promise.resolve(liveOrgs.find((o) => String(o._id) === String(filter._id)) ?? null)) as any)
    sinon.stub(Org, 'find').callsFake((() => Promise.resolve(liveOrgs)) as any)
  })

  afterEach(() => {
    sinon.restore()
  })

  function scopedRequest(tokenPayload: Record<string, unknown>): any {
    return { headers: {}, params: {}, body: {}, query: {}, tokenPayload }
  }

  it('resolves the member of the bot\'s org, not another org\'s member with the same email', async () => {
    const req = scopedRequest({ email: EMAIL, orgId: orgA.toString() })

    await hydrateScopedRequestAsUser(req, appConfig)

    expect(String(req.user.userId)).to.equal(String(userInA._id))
    expect(String(req.user.orgId)).to.equal(String(orgA))
    const filter = usersFindOne.firstCall.args[0]
    expect(String(filter.orgId)).to.equal(String(orgA))
    expect(filter.email).to.equal(EMAIL)
    expect(filter.isDeleted).to.equal(false)
  })

  it('the session it hands downstream is for the bot\'s org', async () => {
    const req = scopedRequest({ email: EMAIL, orgId: orgB.toString() })

    await hydrateScopedRequestAsUser(req, appConfig)

    const token = req.headers.authorization.replace('Bearer ', '')
    const decoded = jwt.verify(token, JWT_SECRET) as any
    expect(decoded.orgId).to.equal(String(orgB))
    expect(decoded.userId).to.equal(String(userInB._id))
  })

  it('does not fall back to another org when the email has no member in the bot\'s org', async () => {
    const orgC = new Types.ObjectId()
    liveOrgs.push({ _id: orgC })
    try {
      const req = scopedRequest({ email: EMAIL, orgId: orgC.toString() })
      await hydrateScopedRequestAsUser(req, appConfig)
      expect.fail('Should have thrown')
    } catch (error: any) {
      expect(error.statusCode).to.equal(404)
    } finally {
      liveOrgs.pop()
    }
    expect(usersFindOne.calledOnce).to.be.true
  })

  it('refuses a token naming an org that does not exist', async () => {
    const req = scopedRequest({ email: EMAIL, orgId: new Types.ObjectId().toString() })
    try {
      await hydrateScopedRequestAsUser(req, appConfig)
      expect.fail('Should have thrown')
    } catch (error: any) {
      expect(error.statusCode).to.equal(401)
    }
    expect(usersFindOne.called).to.be.false
  })

  it('refuses a malformed org id', async () => {
    const req = scopedRequest({ email: EMAIL, orgId: { $ne: null } })
    try {
      await hydrateScopedRequestAsUser(req, appConfig)
      expect.fail('Should have thrown')
    } catch (error: any) {
      expect(error.statusCode).to.equal(401)
    }
    expect(usersFindOne.called).to.be.false
  })

  it('a token without an org is refused when the instance has more than one org', async () => {
    const req = scopedRequest({ email: EMAIL })
    try {
      await hydrateScopedRequestAsUser(req, appConfig)
      expect.fail('Should have thrown')
    } catch (error: any) {
      expect(error.statusCode).to.equal(401)
    }
    expect(usersFindOne.called).to.be.false
  })

  it('a token without an org resolves inside the only org of a single-org instance', async () => {
    ;(Org.find as sinon.SinonStub).callsFake((() => Promise.resolve([{ _id: orgB }])) as any)
    const req = scopedRequest({ email: EMAIL })

    await hydrateScopedRequestAsUser(req, appConfig)

    expect(String(req.user.userId)).to.equal(String(userInB._id))
    expect(String(usersFindOne.firstCall.args[0].orgId)).to.equal(String(orgB))
  })

  it('leaves an already-authenticated request alone', async () => {
    const req: any = { headers: {}, params: {}, user: { userId: 'u', orgId: 'o' } }
    await hydrateScopedRequestAsUser(req, appConfig)
    expect(usersFindOne.called).to.be.false
    expect(req.user).to.deep.equal({ userId: 'u', orgId: 'o' })
  })
})
