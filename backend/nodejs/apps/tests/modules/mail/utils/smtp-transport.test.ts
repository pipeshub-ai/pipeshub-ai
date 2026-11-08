import 'reflect-metadata'
import { expect } from 'chai'
import {
  smtpSettingsProblem,
  smtpTransportOptions,
} from '../../../../src/modules/mail/utils/smtp-transport'
import { closedPort, startFakeSmtpServer, FakeSmtpServer } from '../../../helpers/fake-smtp-server'

// Against a real SMTP conversation on a loopback port, so what is checked is
// what nodemailer does with these settings, not a stub of it.
describe('mail/utils/smtp-transport', () => {
  describe('smtpSettingsProblem', () => {
    let server: FakeSmtpServer | undefined

    afterEach(async () => {
      await server?.close()
      server = undefined
    })

    const settings = (port: number, username?: string, password?: string) => ({
      host: '127.0.0.1', port, fromEmail: 'noreply@example.com',
      ...(username ? { username } : {}), ...(password ? { password } : {}),
    })

    it('accepts settings the server signs in with', async () => {
      server = await startFakeSmtpServer({ user: 'mailer', pass: 'right' })

      expect(await smtpSettingsProblem(settings(server.port, 'mailer', 'right'))).to.equal(null)
      expect(server.logins).to.deep.equal(['mailer:right'])
    })

    it('reports a rejected username or password as auth', async () => {
      server = await startFakeSmtpServer({ user: 'mailer', pass: 'right' })
      const errors: unknown[] = []

      const problem = await smtpSettingsProblem(settings(server.port, 'mailer', 'wrong'), (e) => errors.push(e))

      expect(problem).to.equal('auth')
      expect(errors).to.have.lengthOf(1, 'the server error is handed over for the log')
    })

    it('reports a host and port nothing answers on as unreachable', async () => {
      expect(await smtpSettingsProblem(settings(await closedPort(), 'mailer', 'right'))).to.equal('unreachable')
    })

    it('reports any other refusal as rejected', async () => {
      server = await startFakeSmtpServer({ user: 'mailer', pass: 'right', greeting: '554 5.7.1 Go away\r\n' })

      expect(await smtpSettingsProblem(settings(server.port, 'mailer', 'right'))).to.equal('rejected')
    })

    it('connects without signing in when no username is set', async () => {
      server = await startFakeSmtpServer({ user: 'mailer', pass: 'right' })

      expect(await smtpSettingsProblem(settings(server.port))).to.equal(null)
      expect(server.logins).to.deep.equal([])
    })
  })

  describe('smtpTransportOptions', () => {
    it('never lets an attachment read a local file or fetch a URL', () => {
      const options = smtpTransportOptions({ host: 'h', port: 25, fromEmail: 'a@b.c' })
      expect(options).to.include({ disableFileAccess: true, disableUrlAccess: true })
    })

    it('speaks TLS from the start on 465 only', () => {
      expect(smtpTransportOptions({ host: 'h', port: 465, fromEmail: 'a@b.c' })).to.include({ port: 465, secure: true })
      for (const port of [25, 587, 2525, 0]) {
        expect(smtpTransportOptions({ host: 'h', port, fromEmail: 'a@b.c' }).secure).to.equal(false)
      }
    })

    it('defaults the port to 587 and signs in only with a username', () => {
      expect(smtpTransportOptions({ host: 'h', port: 0, fromEmail: 'a@b.c' })).to.include({ port: 587 })
      expect(smtpTransportOptions({ host: 'h', port: 25, fromEmail: 'a@b.c' })).not.to.have.property('auth')
      expect(smtpTransportOptions({ host: 'h', port: 25, fromEmail: 'a@b.c', username: 'u', password: 'p' }).auth)
        .to.deep.equal({ user: 'u', pass: 'p' })
    })
  })
})
