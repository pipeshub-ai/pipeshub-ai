import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Users } from '../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema'
import { getCurrentOrgId } from '../../../src/libs/context/orgContext'
import { setActivityRepository } from '../../../src/libs/repositories/activity'
import { setUserRepository } from '../../../src/libs/repositories/user'
import {
  findSessionRefusal,
  findSessionRefusals,
} from '../../../src/libs/utils/user-session.utils'

const USER_A = '64b000000000000000000001'
const USER_B = '64b000000000000000000002'
const ORG = '64b0000000000000000000aa'

function createMockQuery(resolvedValue: any = null) {
  const query: any = {
    select: sinon.stub(),
    sort: sinon.stub(),
    lean: sinon.stub(),
    exec: sinon.stub().resolves(resolvedValue),
  }
  query.select.returns(query)
  query.sort.returns(query)
  query.lean.returns(query)
  return query
}

const logger = { error: sinon.stub() } as any

describe('user-session.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('findSessionRefusal', () => {
    it('refuses a token without a role before reading the database', async () => {
      const users = sinon.stub(Users, 'findOne')
      const refusal = await findSessionRefusal({ userId: USER_A, orgId: ORG }, logger)
      expect(refusal).to.equal('Session expired, please login again')
      expect(users.called).to.equal(false)
    })

    it('refuses a removed user', async () => {
      sinon.stub(Users, 'findOne').returns(createMockQuery(null))
      sinon.stub(UserActivities, 'findOne').returns(createMockQuery(null))
      const refusal = await findSessionRefusal(
        { userId: USER_A, orgId: ORG, role: 'member', iat: 100 },
        logger,
      )
      expect(refusal).to.equal('User not found, please login again')
    })

    it('accepts an active user with no later session-ending activity', async () => {
      sinon.stub(Users, 'findOne').returns(createMockQuery({ _id: USER_A }))
      sinon.stub(UserActivities, 'findOne').returns(createMockQuery(null))
      const refusal = await findSessionRefusal(
        { userId: USER_A, orgId: ORG, role: 'member', iat: 100 },
        logger,
      )
      expect(refusal).to.equal(null)
    })
  })

  describe('findSessionRefusals', () => {
    it('returns [] without querying when there are no sessions', async () => {
      const users = sinon.stub(Users, 'find')
      expect(await findSessionRefusals([])).to.deep.equal([])
      expect(users.called).to.equal(false)
    })

    it('refuses removed, disabled and logged-out sessions, in input order', async () => {
      const iat = 1_000
      sinon.stub(Users, 'find').returns(
        createMockQuery([{ _id: USER_B, isDisabled: true }]),
      )
      // Only USER_A's newest activity counts, and it ended the session.
      sinon.stub(UserActivities, 'find').returns(
        createMockQuery([
          { userId: USER_B, orgId: ORG, activityType: 'LOGOUT', createdAt: new Date(iat * 1000 + 5_000) },
        ]),
      )

      const refusals = await findSessionRefusals([
        { userId: USER_A, orgId: ORG, iat },
        { userId: USER_B, orgId: ORG, iat },
      ])

      expect(refusals).to.deep.equal([
        'User not found, please login again',
        'This account is disabled',
      ])
    })

    it('reads each organisation\'s sessions as that organisation, in two queries per organisation', async () => {
      const ORG_B = '64b0000000000000000000bb'
      const seen: Array<[string, string | undefined]> = []
      setUserRepository({
        listLiveFieldsByIds: async (ids: string[]) => (seen.push(['users', getCurrentOrgId()]), ids.map((_id) => ({ _id }))),
      } as never)
      setActivityRepository({
        listSessionEndingSince: async () => (seen.push(['activities', getCurrentOrgId()]), []),
      } as never)
      try {
        const refusals = await findSessionRefusals([
          { userId: USER_A, orgId: ORG, iat: 1 },
          { userId: USER_B, orgId: ORG_B, iat: 1 },
          { userId: USER_B, orgId: ORG, iat: 1 },
        ])
        expect(refusals).to.deep.equal([null, null, null])
        expect(seen.filter(([, o]) => o === ORG)).to.have.length(2)
        expect(seen.filter(([, o]) => o === ORG_B)).to.have.length(2)
      } finally {
        setUserRepository()
        setActivityRepository()
      }
    })

    it('ends a session only when the latest activity came after its token', async () => {
      const iat = 1_000
      sinon.stub(Users, 'find').returns(createMockQuery([{ _id: USER_A }]))
      const activities = sinon.stub(UserActivities, 'find').returns(
        createMockQuery([
          { userId: USER_A, orgId: ORG, activityType: 'LOGOUT', createdAt: new Date(iat * 1000 + 5_000) },
        ]),
      )

      const refusals = await findSessionRefusals([
        { userId: USER_A, orgId: ORG, iat },
        { userId: USER_A, orgId: ORG, iat: iat + 60 },
      ])

      expect(refusals).to.deep.equal(['Session expired, please login again', null])
      const filter = activities.firstCall.args[0] as any
      expect(filter.createdAt.$gte.getTime()).to.equal(iat * 1000)
    })
  })
})
