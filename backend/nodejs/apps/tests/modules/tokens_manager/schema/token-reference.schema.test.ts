import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ServiceType,
  TokenEventType,
} from '../../../../src/modules/tokens_manager/schema/token-reference.schema'

describe('tokens_manager/schema/token-reference.schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('ServiceType enum', () => {
    it('should have ONEDRIVE', () => {
      expect(ServiceType.ONEDRIVE).to.equal('ONEDRIVE')
    })

    it('should have GOOGLE_DRIVE', () => {
      expect(ServiceType.GOOGLE_DRIVE).to.equal('GOOGLE_DRIVE')
    })

    it('should have CONFLUENCE', () => {
      expect(ServiceType.CONFLUENCE).to.equal('CONFLUENCE')
    })

    it('should have JIRA', () => {
      expect(ServiceType.JIRA).to.equal('JIRA')
    })

    it('should have NOTION', () => {
      expect(ServiceType.NOTION).to.equal('NOTION')
    })

    it('should have exactly 5 service types', () => {
      // Enum has both forward and reverse mappings in compiled JS
      const values = Object.values(ServiceType).filter(
        (v) => typeof v === 'string',
      )
      expect(values).to.have.lengthOf(5)
    })
  })

  describe('TokenEventType enum', () => {
    it('should have TOKEN_CREATED', () => {
      expect(TokenEventType.TOKEN_CREATED).to.equal('TOKEN_CREATED')
    })

    it('should have TOKEN_REFRESHED', () => {
      expect(TokenEventType.TOKEN_REFRESHED).to.equal('TOKEN_REFRESHED')
    })

    it('should have TOKEN_REVOKED', () => {
      expect(TokenEventType.TOKEN_REVOKED).to.equal('TOKEN_REVOKED')
    })

    it('should have TOKEN_EXPIRED', () => {
      expect(TokenEventType.TOKEN_EXPIRED).to.equal('TOKEN_EXPIRED')
    })

    it('should have exactly 4 event types', () => {
      const values = Object.values(TokenEventType).filter(
        (v) => typeof v === 'string',
      )
      expect(values).to.have.lengthOf(4)
    })
  })

  describe('ITokenEvent interface', () => {
    it('should allow creating objects conforming to ITokenEvent shape', () => {
      const event: import('../../../../src/modules/tokens_manager/schema/token-reference.schema').ITokenEvent = {
        eventId: 'evt-123',
        eventType: TokenEventType.TOKEN_CREATED,
        tokenReferenceId: 'ref-456',
        serviceType: ServiceType.GOOGLE_DRIVE,
        accountId: 'acc-789',
        timestamp: Date.now(),
      }
      expect(event.eventId).to.equal('evt-123')
      expect(event.eventType).to.equal(TokenEventType.TOKEN_CREATED)
      expect(event.serviceType).to.equal(ServiceType.GOOGLE_DRIVE)
    })
  })
})
