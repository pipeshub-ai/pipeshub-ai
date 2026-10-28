import { expect } from 'chai'
import { MongoUnitOfWork, mapMongoError, sessionOf } from '../../../src/libs/repositories/mongo'
import { UniqueConstraintError, VersionConflictError } from '../../../src/libs/repositories/errors'

describe('Mongo repository seam', () => {
  describe('mapMongoError', () => {
    it('maps 11000 to UniqueConstraintError with the key names only', () => {
      const e = mapMongoError({ code: 11000, keyPattern: { orgId: 1, name: 1 }, keyValue: { name: 'secret@x.com' } }) as UniqueConstraintError
      expect(e).to.be.instanceOf(UniqueConstraintError)
      expect(e.statusCode).to.equal(409)
      expect(e.metadata).to.deep.equal({ key: 'orgId,name' })
    })
    it('maps a mongoose VersionError', () => {
      expect(mapMongoError({ name: 'VersionError' })).to.be.instanceOf(VersionConflictError)
    })
    it('passes anything else through unchanged', () => {
      const e = new Error('x')
      expect(mapMongoError(e)).to.equal(e)
      expect(mapMongoError(null)).to.equal(null)
    })
  })

  describe('MongoUnitOfWork without a replica set', () => {
    it('runs the callback directly, with no session', async () => {
      const out = await new MongoUnitOfWork(false).run(async (tx) => (sessionOf(tx) === undefined ? 'no-session' : 'session'))
      expect(out).to.equal('no-session')
    })
    it('maps a duplicate key thrown by the callback', async () => {
      let caught: unknown
      try {
        await new MongoUnitOfWork(false).run(async () => {
          throw Object.assign(new Error('E11000 duplicate key'), { code: 11000, keyPattern: { email: 1 } })
        })
      } catch (e) {
        caught = e
      }
      expect(caught).to.be.instanceOf(UniqueConstraintError)
    })
  })
})
