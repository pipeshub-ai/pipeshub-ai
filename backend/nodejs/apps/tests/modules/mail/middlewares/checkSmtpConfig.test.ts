import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { smtpConfigChecker } from '../../../../src/modules/mail/middlewares/checkSmtpConfig'
import { SmtpSettingsUnreadableError } from '../../../../src/modules/mail/services/mail.sender.service'

/** A container whose MailSenderService resolves `smtp`, as the stored settings do. */
function containerResolving(smtp: unknown): any {
  return { get: sinon.stub().returns({ getSmtpConfig: sinon.stub().resolves(smtp) }) }
}

describe('mail/middlewares/checkSmtpConfig', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should call next() when smtp is properly configured', async () => {
    const req: any = {
      container: containerResolving({ host: 'smtp.test.com', port: 587, fromEmail: 'noreply@test.com' }),
    }
    const next = sinon.stub()

    await smtpConfigChecker(req, {} as any, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args).to.have.lengthOf(0)
  })

  it('answers 503, not "not configured", when the stored settings cannot be read', async () => {
    const container: any = {
      get: sinon.stub().returns({
        getSmtpConfig: sinon.stub().rejects(new SmtpSettingsUnreadableError(new Error('kv down'))),
      }),
    }
    const next = sinon.stub()

    await smtpConfigChecker({ container } as any, {} as any, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.have.property('statusCode', 503)
  })

  it('should call next with error when container is missing', async () => {
    const next = sinon.stub()

    await smtpConfigChecker({} as any, {} as any, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })

  it('should call next with error when smtp is null', async () => {
    const next = sinon.stub()

    await smtpConfigChecker({ container: containerResolving(null) } as any, {} as any, next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })

  it('should call next with error when smtp host is missing', async () => {
    const next = sinon.stub()

    await smtpConfigChecker(
      { container: containerResolving({ port: 587, fromEmail: 'noreply@test.com' }) } as any,
      {} as any,
      next,
    )

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })

  it('should call next with error when smtp port is missing', async () => {
    const next = sinon.stub()

    await smtpConfigChecker(
      { container: containerResolving({ host: 'smtp.test.com', fromEmail: 'noreply@test.com' }) } as any,
      {} as any,
      next,
    )

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
  })
})
