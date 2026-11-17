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

    // The link is signed to last 20 minutes (jwtGeneratorForValidateEmailLink).
    it('resetEmail should tell the reader the link lasts 20 minutes', () => {
      const result = emailTemplates.resetEmail({ name: 'John', link: 'http://example.com' })
      expect(result).to.include('this link will expire in 20 minutes')
      expect(result).to.not.include('14 days')
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

    it('orgEmailVerification should compile and return HTML', () => {
      const result = emailTemplates.orgEmailVerification({
        name: 'Acme Corp',
        link: 'http://example.com/verify',
      })
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
  describe('sandboxCapacityAlert', () => {
    // Built the same way the platform alert handler builds it.
    let build: typeof import('../../../../src/modules/notification/utils/sandbox-capacity-mail').buildSandboxCapacityMail
    let getEmailContent: typeof import('../../../../src/modules/mail/utils/email-content').getEmailContent

    before(() => {
      build = require('../../../../src/modules/notification/utils/sandbox-capacity-mail').buildSandboxCapacityMail
      getEmailContent = require('../../../../src/modules/mail/utils/email-content').getEmailContent
    })

    const event = {
      title: 'Code runs are waiting for capacity',
      message: 'm',
      redirectLink: 'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts',
    }
    const payload = (overrides: Record<string, unknown> = {}) => ({
      level: 'warning',
      host: 'pipeshub-7d9c',
      memorySource: 'host',
      windowMinutes: 30,
      runs: 412,
      waited: 57,
      refused: 0,
      busyPercent: 72,
      refusedPercent: 0,
      longestWaitSeconds: 118,
      slotWaitSeconds: 120,
      reason: 'memory',
      reasonText: 'Free memory on the host was low.',
      limit: { current: 3, ceiling: 6, floor: 2 },
      hostMemoryTotalGb: 16,
      hostMemoryFreeGb: 1.1,
      ...overrides,
    })
    const render = (overrides: Record<string, unknown> = {}, ev: Record<string, unknown> = event) =>
      getEmailContent('sandboxCapacityAlert', build(ev, payload(overrides) as never)!.templateData as never)

    it('lists the action for each reason', () => {
      expect(render({ reason: 'memory' })).to.contain('Add memory to the host')
      expect(render({ reason: 'cpu' })).to.contain('Add CPU cores to the host')
      expect(render({ reason: 'slots' })).to.contain('SANDBOX_MAX_TOTAL')
      expect(render()).to.contain('load alerts guide')
    })

    it('shows the indexing paragraph only when the event has one', () => {
      expect(render()).to.not.contain('Already done')
      const html = render({ indexingText: 'Indexing was already slowed down from 10:13 UTC to give chat priority.' })
      expect(html).to.contain('Already done')
      expect(html).to.contain('10:13 UTC')
    })

    it('labels the memory row for the code runner and uses its action when the memory is the sidecar', () => {
      const html = render({ memorySource: 'sidecar' })
      expect(html).to.contain('Code runner memory free / total')
      expect(html).to.contain('sandbox.dind.resources')
      expect(render()).to.contain('Host memory free / total')
    })

    it('leaves the memory row out when the values are unknown', () => {
      const html = render({ hostMemoryTotalGb: null, hostMemoryFreeGb: null })
      expect(html).to.not.contain('memory free / total')
    })

    it('escapes HTML from the event', () => {
      const html = render({ reasonText: '<script>alert(1)</script>' }, { ...event, title: '<b>x</b>' })
      expect(html).to.not.contain('<script>alert(1)</script>')
      expect(html).to.contain('&lt;script&gt;')
      expect(html).to.not.contain('<b>x</b>')
    })

    it('puts the brand product name in the subject', () => {
      expect(build(event, payload() as never)!.subject).to.equal('PipesHub: code runs in chat are waiting for capacity')
    })

    it('has no email for a notice that is not a warning or critical', () => {
      expect(build(event, payload({ level: 'resolved' }) as never)).to.equal(null)
    })

    it('refuses data missing the required fields', () => {
      expect(() => getEmailContent('sandboxCapacityAlert', { heading: 'x' })).to.throw('sandboxCapacityAlert requires')
    })
  })
})
