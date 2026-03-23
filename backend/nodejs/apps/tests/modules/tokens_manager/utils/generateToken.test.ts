import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as createJwtModule from '../../../../src/libs/utils/createJwt'
import { generateFetchConfigToken } from '../../../../src/modules/tokens_manager/utils/generateToken'

describe('tokens_manager/utils/generateToken', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('generateFetchConfigToken', () => {
    it('should call fetchConfigJwtGenerator with correct params', async () => {
      const stub = sinon.stub(createJwtModule, 'fetchConfigJwtGenerator').resolves('mock-token')
      const user = { userId: 'user123', orgId: 'org456' }
      const secret = 'test-secret'

      const result = await generateFetchConfigToken(user, secret)

      expect(result).to.equal('mock-token')
      expect(stub.calledOnceWith('user123', 'org456', 'test-secret')).to.be.true
    })

    it('should propagate errors from fetchConfigJwtGenerator', async () => {
      sinon.stub(createJwtModule, 'fetchConfigJwtGenerator').rejects(new Error('JWT generation failed'))
      const user = { userId: 'user123', orgId: 'org456' }

      try {
        await generateFetchConfigToken(user, 'secret')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('JWT generation failed')
      }
    })
  })
})
