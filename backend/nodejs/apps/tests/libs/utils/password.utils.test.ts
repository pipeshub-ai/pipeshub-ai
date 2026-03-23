import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { passwordValidator } from '../../../src/libs/utils/password.utils'

describe('password.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('passwordValidator', () => {
    it('should accept a valid password with all requirements', () => {
      expect(passwordValidator('Abcdef1!')).to.be.true
    })

    it('should accept a strong password', () => {
      expect(passwordValidator('MyP@ssw0rd!')).to.be.true
    })

    it('should accept a password with multiple special characters', () => {
      expect(passwordValidator('Aa1!@#$%^&*-')).to.be.true
    })

    it('should accept a long password', () => {
      expect(passwordValidator('ThisIsAVeryLongPassword1!')).to.be.true
    })

    it('should reject a password shorter than 8 characters', () => {
      expect(passwordValidator('Aa1!')).to.be.false
    })

    it('should reject a password without uppercase letters', () => {
      expect(passwordValidator('abcdef1!')).to.be.false
    })

    it('should reject a password without lowercase letters', () => {
      expect(passwordValidator('ABCDEF1!')).to.be.false
    })

    it('should reject a password without numbers', () => {
      expect(passwordValidator('Abcdefg!')).to.be.false
    })

    it('should reject a password without special characters', () => {
      expect(passwordValidator('Abcdef12')).to.be.false
    })

    it('should reject an empty string', () => {
      expect(passwordValidator('')).to.be.false
    })

    it('should reject a password of only spaces', () => {
      expect(passwordValidator('        ')).to.be.false
    })

    it('should reject a password of only digits', () => {
      expect(passwordValidator('12345678')).to.be.false
    })

    it('should reject a password of only lowercase', () => {
      expect(passwordValidator('abcdefgh')).to.be.false
    })

    it('should reject a password of only uppercase', () => {
      expect(passwordValidator('ABCDEFGH')).to.be.false
    })

    it('should reject a password of only special characters', () => {
      expect(passwordValidator('!@#$%^&*')).to.be.false
    })

    it('should accept exactly 8-character valid password', () => {
      expect(passwordValidator('Aa1!bbbb')).to.be.true
    })

    it('should accept passwords with supported special characters #?!@$%^&*-', () => {
      const specials = ['#', '?', '!', '@', '$', '%', '^', '&', '*', '-']
      for (const s of specials) {
        expect(
          passwordValidator(`Abcdef1${s}`),
          `Failed for special char: ${s}`,
        ).to.be.true
      }
    })
  })
})
