/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { assertAuthMethodEnabled, IOrgAuthConfigLike } from '../../../../src/modules/auth/utils/authMethodGuard'
import { AuthMethodType } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema'

describe('authMethodGuard', () => {
  describe('assertAuthMethodEnabled', () => {
    it('should throw NotFoundError when orgAuthConfig is null', () => {
      expect(() => assertAuthMethodEnabled(null, AuthMethodType.PASSWORD))
        .to.throw('Auth configuration not found for this organization')
    })

    it('should throw NotFoundError when orgAuthConfig is undefined', () => {
      expect(() => assertAuthMethodEnabled(undefined, AuthMethodType.PASSWORD))
        .to.throw('Auth configuration not found for this organization')
    })

    it('should not throw when the method is allowed', () => {
      const config: IOrgAuthConfigLike = {
        authSteps: [
          { allowedMethods: [{ type: 'password' }] },
        ],
      }
      expect(() => assertAuthMethodEnabled(config, AuthMethodType.PASSWORD)).to.not.throw()
    })

    it('should throw ForbiddenError when the method is not allowed', () => {
      const config: IOrgAuthConfigLike = {
        authSteps: [
          { allowedMethods: [{ type: 'password' }] },
        ],
      }
      expect(() => assertAuthMethodEnabled(config, AuthMethodType.OTP))
        .to.throw('"otp" authentication is not enabled for this organization')
    })

    it('should check across multiple auth steps', () => {
      const config: IOrgAuthConfigLike = {
        authSteps: [
          { allowedMethods: [{ type: 'password' }] },
          { allowedMethods: [{ type: 'otp' }] },
        ],
      }
      expect(() => assertAuthMethodEnabled(config, AuthMethodType.OTP)).to.not.throw()
    })

    it('should throw when authSteps is empty', () => {
      const config: IOrgAuthConfigLike = {
        authSteps: [],
      }
      expect(() => assertAuthMethodEnabled(config, AuthMethodType.PASSWORD))
        .to.throw('"password" authentication is not enabled for this organization')
    })

    it('should check across multiple methods within a step', () => {
      const config: IOrgAuthConfigLike = {
        authSteps: [
          { allowedMethods: [{ type: 'password' }, { type: 'google' }] },
        ],
      }
      expect(() => assertAuthMethodEnabled(config, AuthMethodType.GOOGLE)).to.not.throw()
    })
  })
})
