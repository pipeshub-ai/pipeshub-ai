import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('mail/config/config', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/mail/config/config')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/mail/config/config')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('MailConfig interface', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../src/modules/mail/config/config').MailConfig = {
        jwtSecret: 'super-secret-key',
        scopedJwtSecret: 'scoped-secret-key',
        database: {
          url: 'mongodb://localhost:27017',
          dbName: 'pipeshub',
        },
        smtp: {
          username: 'smtp-user',
          password: 'smtp-pass',
          host: 'smtp.example.com',
          port: 587,
          fromEmail: 'noreply@example.com',
        },
      }
      expect(config.jwtSecret).to.equal('super-secret-key')
      expect(config.database.url).to.equal('mongodb://localhost:27017')
      expect(config.smtp.host).to.equal('smtp.example.com')
      expect(config.smtp.port).to.equal(587)
      expect(config.smtp.fromEmail).to.equal('noreply@example.com')
    })
  })
})
