import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  CONFIDENCE_LEVELS,
  CONVERSATION_STATUS,
  FAIL_REASON_TYPE,
} from '../../../../src/modules/enterprise_search/constants/constants'

describe('enterprise_search/constants/constants', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('CONFIDENCE_LEVELS', () => {
    it('should have HIGH as "High"', () => {
      expect(CONFIDENCE_LEVELS.HIGH).to.equal('High')
    })

    it('should have MEDIUM as "Medium"', () => {
      expect(CONFIDENCE_LEVELS.MEDIUM).to.equal('Medium')
    })

    it('should have LOW as "Low"', () => {
      expect(CONFIDENCE_LEVELS.LOW).to.equal('Low')
    })

    it('should have VERY_HIGH as "Very High"', () => {
      expect(CONFIDENCE_LEVELS.VERY_HIGH).to.equal('Very High')
    })

    it('should have UNKNOWN as "Unknown"', () => {
      expect(CONFIDENCE_LEVELS.UNKNOWN).to.equal('Unknown')
    })

    it('should have exactly 5 levels', () => {
      expect(Object.keys(CONFIDENCE_LEVELS)).to.have.lengthOf(5)
    })

    it('should contain only the expected keys', () => {
      const expectedKeys = ['HIGH', 'MEDIUM', 'LOW', 'VERY_HIGH', 'UNKNOWN']
      expect(Object.keys(CONFIDENCE_LEVELS)).to.have.members(expectedKeys)
    })
  })

  describe('CONVERSATION_STATUS', () => {
    it('should have COMPLETE as "Complete"', () => {
      expect(CONVERSATION_STATUS.COMPLETE).to.equal('Complete')
    })

    it('should have FAILED as "Failed"', () => {
      expect(CONVERSATION_STATUS.FAILED).to.equal('Failed')
    })

    it('should have INPROGRESS as "Inprogress"', () => {
      expect(CONVERSATION_STATUS.INPROGRESS).to.equal('Inprogress')
    })

    it('should have NONE as "None"', () => {
      expect(CONVERSATION_STATUS.NONE).to.equal('None')
    })

    it('should have exactly 4 statuses', () => {
      expect(Object.keys(CONVERSATION_STATUS)).to.have.lengthOf(4)
    })

    it('should contain only the expected keys', () => {
      const expectedKeys = ['COMPLETE', 'FAILED', 'INPROGRESS', 'NONE']
      expect(Object.keys(CONVERSATION_STATUS)).to.have.members(expectedKeys)
    })
  })

  describe('FAIL_REASON_TYPE', () => {
    it('should have AI_SERVICE_UNAVAILABLE', () => {
      expect(FAIL_REASON_TYPE.AI_SERVICE_UNAVAILABLE).to.equal('ai_service_unavailable')
    })

    it('should have AI_SERVICE_ERROR', () => {
      expect(FAIL_REASON_TYPE.AI_SERVICE_ERROR).to.equal('ai_service_error')
    })

    it('should have AI_API_ERROR', () => {
      expect(FAIL_REASON_TYPE.AI_API_ERROR).to.equal('ai_api_error')
    })

    it('should have CONNECTION_ERROR', () => {
      expect(FAIL_REASON_TYPE.CONNECTION_ERROR).to.equal('connection_error')
    })

    it('should have INTERNAL_ERROR', () => {
      expect(FAIL_REASON_TYPE.INTERNAL_ERROR).to.equal('internal_error')
    })

    it('should have INVALID_REQUEST', () => {
      expect(FAIL_REASON_TYPE.INVALID_REQUEST).to.equal('invalid_request')
    })

    it('should have TIMEOUT', () => {
      expect(FAIL_REASON_TYPE.TIMEOUT).to.equal('timeout')
    })

    it('should have PROCESSING_ERROR', () => {
      expect(FAIL_REASON_TYPE.PROCESSING_ERROR).to.equal('processing_error')
    })

    it('should have UNKNOWN_ERROR', () => {
      expect(FAIL_REASON_TYPE.UNKNOWN_ERROR).to.equal('unknown_error')
    })

    it('should have exactly 9 fail reason types', () => {
      expect(Object.keys(FAIL_REASON_TYPE)).to.have.lengthOf(9)
    })

    it('should have all values as strings', () => {
      Object.values(FAIL_REASON_TYPE).forEach((value) => {
        expect(value).to.be.a('string')
      })
    })

    it('should have no duplicate values', () => {
      const values = Object.values(FAIL_REASON_TYPE)
      const uniqueValues = new Set(values)
      expect(uniqueValues.size).to.equal(values.length)
    })
  })
})
