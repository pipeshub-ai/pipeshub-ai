import { expect } from 'chai'
import jwt from 'jsonwebtoken'
import sinon from 'sinon'
import {
  createSetPasswordLink,
  main,
  USAGE,
} from '../../src/scripts/set-password-link'
import { passwordResetLinkLifetime } from '../../src/libs/utils/createJwt'
import { deriveUserActionSecret } from '../../src/libs/utils/jwtKeys'
import { TokenScopes } from '../../src/libs/enums/token-scopes.enum'
import { Users } from '../../src/modules/user_management/schema/users.schema'

const config = {
  frontendUrl: 'https://pipeshub.example',
  scopedJwtSecret: 'scoped-secret-for-tests',
}
const user = { _id: 'user-1', orgId: 'org-1', email: 'alice@acme.example' }

describe('scripts/set-password-link', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createSetPasswordLink', () => {
    it('looks the person up by their trimmed, lowercased address', async () => {
      const findUser = sinon.stub().resolves(user)

      await createSetPasswordLink('  Alice@ACME.example ', config, findUser)

      expect(findUser.calledOnceWithExactly('alice@acme.example')).to.be.true
    })

    it('finds active accounts in the users collection by default', async () => {
      const exec = sinon.stub().resolves(user)
      const lean = sinon.stub().returns({ exec })
      const select = sinon.stub().returns({ lean })
      const findOne = sinon.stub(Users, 'findOne').returns({ select } as any)

      await createSetPasswordLink('alice@acme.example', config)

      expect(findOne.firstCall.args[0]).to.deep.equal({
        email: 'alice@acme.example',
        isDeleted: { $ne: true },
      })
      expect(select.firstCall.args[0]).to.equal('_id orgId email')
    })

    it('refuses an address no active account uses', async () => {
      const findUser = sinon.stub().resolves(null)

      try {
        await createSetPasswordLink('nobody@acme.example', config, findUser)
        expect.fail('should have rejected')
      } catch (error) {
        expect((error as Error).message).to.equal(
          'No active PipesHub account uses nobody@acme.example. Check the address and try again.',
        )
      }
    })

    it('returns a reset-password link carrying a password-reset token', async () => {
      const { link, lifetime } = await createSetPasswordLink(
        'alice@acme.example',
        config,
        sinon.stub().resolves(user),
      )

      const prefix = `${config.frontendUrl}/reset-password#token=`
      expect(link.startsWith(prefix)).to.be.true
      const payload = jwt.verify(
        link.slice(prefix.length),
        deriveUserActionSecret(config.scopedJwtSecret),
      ) as jwt.JwtPayload
      expect(payload).to.include({
        userEmail: 'alice@acme.example',
        userId: 'user-1',
        orgId: 'org-1',
      })
      expect(payload.scopes).to.deep.equal([TokenScopes.PASSWORD_RESET])
      expect(lifetime).to.equal(passwordResetLinkLifetime().description)
    })
  })

  describe('main', () => {
    for (const args of [[], ['a@acme.example', 'b@acme.example']]) {
      it(`prints usage for ${args.length} arguments`, async () => {
        const out = sinon.stub()
        const err = sinon.stub()

        expect(await main(args, out, err)).to.equal(2)

        expect(err.calledOnceWithExactly(USAGE)).to.be.true
        expect(out.called).to.be.false
      })
    }
  })
})
