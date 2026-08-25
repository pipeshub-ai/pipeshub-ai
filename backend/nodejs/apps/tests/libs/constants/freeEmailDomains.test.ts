/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { FREE_EMAIL_DOMAINS, isFreeEmailDomain } from '../../../src/libs/constants/freeEmailDomains'

describe('freeEmailDomains', () => {
  describe('FREE_EMAIL_DOMAINS', () => {
    it('should be a Set of strings', () => {
      expect(FREE_EMAIL_DOMAINS).to.be.instanceOf(Set)
      expect(FREE_EMAIL_DOMAINS.size).to.be.greaterThan(50)
    })

    it('should contain common free providers', () => {
      expect(FREE_EMAIL_DOMAINS.has('gmail.com')).to.be.true
      expect(FREE_EMAIL_DOMAINS.has('outlook.com')).to.be.true
      expect(FREE_EMAIL_DOMAINS.has('yahoo.com')).to.be.true
      expect(FREE_EMAIL_DOMAINS.has('hotmail.com')).to.be.true
      expect(FREE_EMAIL_DOMAINS.has('icloud.com')).to.be.true
      expect(FREE_EMAIL_DOMAINS.has('protonmail.com')).to.be.true
      expect(FREE_EMAIL_DOMAINS.has('proton.me')).to.be.true
    })

    it('should not contain business domains', () => {
      expect(FREE_EMAIL_DOMAINS.has('acme.com')).to.be.false
      expect(FREE_EMAIL_DOMAINS.has('company.io')).to.be.false
    })
  })

  describe('isFreeEmailDomain', () => {
    it('should return true for a known free domain', () => {
      expect(isFreeEmailDomain('gmail.com')).to.be.true
      expect(isFreeEmailDomain('yahoo.com')).to.be.true
    })

    it('should return false for a business domain', () => {
      expect(isFreeEmailDomain('acme.com')).to.be.false
    })

    it('should be case-insensitive', () => {
      expect(isFreeEmailDomain('Gmail.Com')).to.be.true
      expect(isFreeEmailDomain('OUTLOOK.COM')).to.be.true
    })
  })
})
