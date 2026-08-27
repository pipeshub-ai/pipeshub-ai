/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { orgContextStorage, getCurrentOrgId, OrgContext } from '../../../src/libs/context/orgContext'

describe('orgContext', () => {
  describe('orgContextStorage', () => {
    it('should be an AsyncLocalStorage instance', () => {
      expect(orgContextStorage).to.exist
      expect(orgContextStorage.getStore).to.be.a('function')
      expect(orgContextStorage.run).to.be.a('function')
    })
  })

  describe('getCurrentOrgId', () => {
    it('should return undefined when no store is active', () => {
      const result = getCurrentOrgId()
      expect(result).to.be.undefined
    })

    it('should return the orgId from the active store', (done) => {
      const ctx: OrgContext = { orgId: 'org-123' }
      orgContextStorage.run(ctx, () => {
        const result = getCurrentOrgId()
        expect(result).to.equal('org-123')
        done()
      })
    })
  })
})
