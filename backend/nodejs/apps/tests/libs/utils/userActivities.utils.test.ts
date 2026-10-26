import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { activityEndsSession, SESSION_INVALIDATING_ACTIVITIES, userActivitiesType } from '../../../src/libs/utils/userActivities.utils'

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

    it('should have ACCOUNT_DELETED activity type', () => {
      expect(userActivitiesType.ACCOUNT_DELETED).to.equal('ACCOUNT DELETED')
    })

    it('should have exactly 12 activity types', () => {
      expect(Object.keys(userActivitiesType)).to.have.length(12)
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
        userActivitiesType.ACCOUNT_DELETED,
        userActivitiesType.ACCOUNT_RESTORED,
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

  describe('activityEndsSession', () => {
    const issuedAt = 1_700_000_000
    const at = (ms: number) => new Date(issuedAt * 1000 + ms)

    it('ends tokens from a deletion or a restore in the same second, without the allowance', () => {
      for (const activityType of ['ACCOUNT DELETED', 'ACCOUNT RESTORED']) {
        expect(activityEndsSession({ activityType, createdAt: at(500) }, issuedAt)).to.be.true
        expect(activityEndsSession({ activityType, createdAt: at(-1) }, issuedAt)).to.be.false
      }
    })

    it('gives other activities a one-second allowance', () => {
      expect(activityEndsSession({ activityType: 'PASSWORD CHANGED', createdAt: at(500) }, issuedAt)).to.be.false
      expect(activityEndsSession({ activityType: 'PASSWORD CHANGED', createdAt: at(1500) }, issuedAt)).to.be.true
    })

    it('counts a restore as session-ending', () => {
      expect(SESSION_INVALIDATING_ACTIVITIES).to.include('ACCOUNT RESTORED')
    })
  })
})
