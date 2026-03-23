import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { smtpConfigChecker } from '../../../../src/modules/mail/middlewares/checkSmtpConfig'

describe('mail/middlewares/checkSmtpConfig', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should call next() when smtp is properly configured', () => {
    const mockContainer = {
      get: sinon.stub().returns({
        smtp: { host: 'smtp.test.com', port: 587, fromEmail: 'noreply@test.com' },
      }),
    }
    const req: any = { container: mockContainer }
    const res: any = {}
    const next = sinon.stub()

    smtpConfigChecker(req, res, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args).to.have.lengthOf(0)
  })

  it('should call next with error when container is missing', () => {
    const req: any = {}
    const res: any = {}
    const next = sinon.stub()

    smtpConfigChecker(req, res, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })

  it('should call next with error when smtp is null', () => {
    const mockContainer = {
      get: sinon.stub().returns({ smtp: null }),
    }
    const req: any = { container: mockContainer }
    const res: any = {}
    const next = sinon.stub()

    smtpConfigChecker(req, res, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })

  it('should call next with error when smtp host is missing', () => {
    const mockContainer = {
      get: sinon.stub().returns({
        smtp: { port: 587, fromEmail: 'noreply@test.com' },
      }),
    }
    const req: any = { container: mockContainer }
    const res: any = {}
    const next = sinon.stub()

    smtpConfigChecker(req, res, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })

  it('should call next with error when smtp port is missing', () => {
    const mockContainer = {
      get: sinon.stub().returns({
        smtp: { host: 'smtp.test.com', fromEmail: 'noreply@test.com' },
      }),
    }
    const req: any = { container: mockContainer }
    const res: any = {}
    const next = sinon.stub()

    smtpConfigChecker(req, res, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })
})
