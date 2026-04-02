import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NOTIFICATION_EVENTS } from '../../../../src/modules/notification/constants/constants'

describe('notification/constants/constants', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('NOTIFICATION_EVENTS', () => {
    it('should be exported', () => {
      expect(NOTIFICATION_EVENTS).to.exist
    })

    it('should have FILE_UPLOAD_STATUS', () => {
      expect(NOTIFICATION_EVENTS.FILE_UPLOAD_STATUS).to.equal('FILE_UPLOAD_STATUS')
    })

    it('should have exactly 1 event', () => {
      expect(Object.keys(NOTIFICATION_EVENTS)).to.have.lengthOf(1)
    })

    it('should have all values as strings', () => {
      Object.values(NOTIFICATION_EVENTS).forEach((value) => {
        expect(value).to.be.a('string')
      })
    })
  })
})
