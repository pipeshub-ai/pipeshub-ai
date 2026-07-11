import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import Handlebars from 'handlebars'

describe('mail/utils/emailTemplates', () => {
  let emailTemplates: typeof import('../../../../src/modules/mail/utils/emailTemplates')

  before(() => {
    emailTemplates = require('../../../../src/modules/mail/utils/emailTemplates')
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('Handlebars helper: checkIfLengthLT5', () => {
    let helper: Function

    before(function () {
      helper = (Handlebars.helpers as any)['checkIfLengthLT5']
      if (!helper) this.skip()
    })

    it('should call inverse when str is null', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, null, options)
      expect(options.inverse.calledOnce).to.be.true
      expect(options.fn.called).to.be.false
    })

    it('should call fn when str.length < 5', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, 'abc', options)
      expect(options.fn.calledOnce).to.be.true
      expect(options.inverse.called).to.be.false
    })

    it('should call inverse when str.length >= 5', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, 'hello world', options)
      expect(options.inverse.calledOnce).to.be.true
      expect(options.fn.called).to.be.false
    })

    it('should call inverse when str is exactly length 5', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, '12345', options)
      expect(options.inverse.calledOnce).to.be.true
    })
  })

  describe('Handlebars helper: checkIfLengthGTE5', () => {
    let helper: Function

    before(function () {
      helper = (Handlebars.helpers as any)['checkIfLengthGTE5']
      if (!helper) this.skip()
    })

    it('should call fn when str is null', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, null, options)
      expect(options.fn.calledOnce).to.be.true
      expect(options.inverse.called).to.be.false
    })

    it('should call fn when str.length >= 5', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, 'hello world', options)
      expect(options.fn.calledOnce).to.be.true
      expect(options.inverse.called).to.be.false
    })

    it('should call inverse when str.length < 5', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, 'abc', options)
      expect(options.inverse.calledOnce).to.be.true
      expect(options.fn.called).to.be.false
    })

    it('should call fn when str is exactly length 5', () => {
      const options = {
        fn: sinon.stub().returns('fn'),
        inverse: sinon.stub().returns('inverse'),
      }
      helper.call({}, '12345', options)
      expect(options.fn.calledOnce).to.be.true
    })
  })

  describe('Template exports', () => {
    it('loginWithOTPRequest should compile and return HTML', () => {
      const result = emailTemplates.loginWithOTPRequest({ name: 'John', otp: '12345' })
      expect(result).to.be.a('string')
      expect(result.length).to.be.greaterThan(0)
    })

    it('suspiciousLoginAttempt should compile and return HTML', () => {
      const result = emailTemplates.suspiciousLoginAttempt({ name: 'John' })
      expect(result).to.be.a('string')
      expect(result.length).to.be.greaterThan(0)
    })

    it('resetPassword should compile and return HTML', () => {
      const result = emailTemplates.resetPassword({ name: 'John', link: 'http://example.com/reset' })
      expect(result).to.be.a('string')
      expect(result.length).to.be.greaterThan(0)
    })

    it('resetEmail should compile and return HTML', () => {
      const result = emailTemplates.resetEmail({ name: 'John', link: 'http://example.com' })
      expect(result).to.be.a('string')
      expect(result.length).to.be.greaterThan(0)
    })

    it('accountCreation should compile and return HTML', () => {
      const result = emailTemplates.accountCreation({ name: 'Acme Corp' })
      expect(result).to.be.a('string')
      expect(result.length).to.be.greaterThan(0)
    })

    it('appUserInvite should compile and return HTML', () => {
      const result = emailTemplates.appUserInvite({ name: 'Jane', link: 'http://invite.example.com' })
      expect(result).to.be.a('string')
      expect(result.length).to.be.greaterThan(0)
    })

    it('should throw when templateData is null (compileTemplate guard)', () => {
      expect(() => emailTemplates.loginWithOTPRequest(null as any)).to.throw('Invalid templateData')
    })

    it('should handle empty templateData object', () => {
      const result = emailTemplates.loginWithOTPRequest({})
      expect(result).to.be.a('string')
    })
  })
})
