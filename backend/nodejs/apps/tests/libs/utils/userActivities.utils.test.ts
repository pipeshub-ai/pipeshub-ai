import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { SESSION_INVALIDATING_ACTIVITIES, userActivitiesType } from '../../../src/libs/utils/userActivities.utils'

describe('userActivities.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('userActivitiesType', () => {
    it('should have LOGIN activity type', () => {
      expect(userActivitiesType.LOGIN).to.equal('LOGIN')
    })

    it('should have LOGOUT activity type', () => {
      expect(userActivitiesType.LOGOUT).to.equal('LOGOUT')
    })

    it('should have OTP_GENERATE activity type', () => {
      expect(userActivitiesType.OTP_GENERATE).to.equal('OTP GENERATE')
    })

    it('should have LOGIN_ATTEMPT activity type', () => {
      expect(userActivitiesType.LOGIN_ATTEMPT).to.equal('LOGIN ATTEMPT')
    })

    it('should have WRONG_PASSWORD activity type', () => {
      expect(userActivitiesType.WRONG_PASSWORD).to.equal('WRONG PASSWORD')
    })

    it('should have WRONG_OTP activity type', () => {
      expect(userActivitiesType.WRONG_OTP).to.equal('WRONG OTP')
    })

    it('should have REFRESH_TOKEN activity type', () => {
      expect(userActivitiesType.REFRESH_TOKEN).to.equal('REFRESH TOKEN')
    })

    it('should have PASSWORD_CHANGED activity type', () => {
      expect(userActivitiesType.PASSWORD_CHANGED).to.equal('PASSWORD CHANGED')
    })

    it('should have ROLE_CHANGED activity type', () => {
      expect(userActivitiesType.ROLE_CHANGED).to.equal('ROLE CHANGED')
    })

    it('should have ACCOUNT_BLOCKED activity type', () => {
      expect(userActivitiesType.ACCOUNT_BLOCKED).to.equal('ACCOUNT BLOCKED')
    })

    it('should have exactly 10 activity types', () => {
      expect(Object.keys(userActivitiesType)).to.have.length(10)
    })

    it('should have unique values for all activity types', () => {
      const values = Object.values(userActivitiesType)
      const uniqueValues = new Set(values)
      expect(uniqueValues.size).to.equal(values.length)
    })
  })

  describe('SESSION_INVALIDATING_ACTIVITIES', () => {
    it('lists every activity that must end a session', () => {
      expect([...SESSION_INVALIDATING_ACTIVITIES]).to.have.members([
        userActivitiesType.LOGOUT,
        userActivitiesType.PASSWORD_CHANGED,
        userActivitiesType.ROLE_CHANGED,
        userActivitiesType.ACCOUNT_BLOCKED,
      ])
    })

    it('leaves activities that must not end a session out', () => {
      const kept = [
        userActivitiesType.LOGIN,
        userActivitiesType.LOGIN_ATTEMPT,
        userActivitiesType.OTP_GENERATE,
        userActivitiesType.REFRESH_TOKEN,
        userActivitiesType.WRONG_OTP,
        userActivitiesType.WRONG_PASSWORD,
      ]
      const invalidating: readonly string[] = SESSION_INVALIDATING_ACTIVITIES
      kept.forEach((activity) => {
        expect(invalidating).to.not.include(activity)
      })
    })
  })
})
