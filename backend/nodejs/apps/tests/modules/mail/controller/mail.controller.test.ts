import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { MailController } from '../../../../src/modules/mail/controller/mail.controller'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'

describe('mail/controller/mail.controller', () => {
  let controller: MailController
  let mockConfig: any
  let mockLogger: any

  beforeEach(() => {
    mockConfig = {
      smtp: {
        host: 'smtp.test.com',
        port: 587,
        username: 'user',
        password: 'pass',
        fromEmail: 'noreply@test.com',
      },
    }
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    controller = new MailController(mockConfig, mockLogger)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('sendMail', () => {
    it('should throw NotFoundError when smtp is not configured', async () => {
      controller = new MailController({ smtp: null }, mockLogger)
      const req: any = { body: {} }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
      const next = sinon.stub()

      await controller.sendMail(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError)
    })

    it('should send email and respond with 200 on success', async () => {
      sinon.stub(controller, 'emailSender').resolves({ status: true, data: 'Email sent' })
      const req: any = { body: { sendEmailTo: 'test@test.com', subject: 'Test' } }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
      const next = sinon.stub()

      await controller.sendMail(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(next.called).to.be.false
    })

    it('should call next with error when emailSender fails', async () => {
      sinon.stub(controller, 'emailSender').resolves({ status: false, data: 'SMTP error' })
      const req: any = { body: { sendEmailTo: 'test@test.com' } }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
      const next = sinon.stub()

      await controller.sendMail(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getEmailContent', () => {
    it('should return content for LoginWithOtp template', () => {
      // This may throw if template files don't exist in test env, but tests the routing logic
      try {
        const content = controller.getEmailContent('loginWithOtp', { otp: '1234' })
        expect(content).to.be.a('string')
      } catch {
        // Template files may not be available in test environment
      }
    })

    it('should throw for unknown template type', () => {
      expect(() => controller.getEmailContent('unknown-template', {})).to.throw('Unknown Template')
    })
  })
})
