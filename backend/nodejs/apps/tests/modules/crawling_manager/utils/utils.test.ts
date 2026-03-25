import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { constructSyncConnectorEvent } from '../../../../src/modules/crawling_manager/utils/utils'

describe('crawling_manager/utils/utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('constructSyncConnectorEvent', () => {
    it('should construct event with correct eventType', () => {
      const event = constructSyncConnectorEvent('org-1', 'Google Drive', 'conn-1')
      expect(event.eventType).to.equal('googledrive.resync')
    })

    it('should set timestamp to current time', () => {
      const before = Date.now()
      const event = constructSyncConnectorEvent('org-1', 'drive', 'conn-1')
      const after = Date.now()
      expect(event.timestamp).to.be.at.least(before)
      expect(event.timestamp).to.be.at.most(after)
    })

    it('should include orgId in payload', () => {
      const event = constructSyncConnectorEvent('org-1', 'drive', 'conn-1')
      expect(event.payload.orgId).to.equal('org-1')
    })

    it('should include connector and connectorId in payload', () => {
      const event = constructSyncConnectorEvent('org-1', 'drive', 'conn-1')
      expect(event.payload.connector).to.equal('drive')
      expect(event.payload.connectorId).to.equal('conn-1')
    })

    it('should set origin to CONNECTOR', () => {
      const event = constructSyncConnectorEvent('org-1', 'drive', 'conn-1')
      expect(event.payload.origin).to.equal('CONNECTOR')
    })

    it('should handle connector names with spaces by removing them', () => {
      const event = constructSyncConnectorEvent('org-1', 'Google Drive', 'conn-1')
      expect(event.eventType).to.equal('googledrive.resync')
    })
  })
})
