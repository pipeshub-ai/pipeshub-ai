import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  RECORD_TYPE,
  ORIGIN_TYPE,
  CONNECTOR_NAME,
  INDEXING_STATUS,
  ENTITY_TYPE,
  COLLECTIONS,
  RELATIONSHIP_TYPE,
  ROLE,
  GRAPHS,
  COLLECTION_TYPE,
  isValidRecordType,
  isValidOriginType,
  isValidConnectorName,
  isValidCollectionName,
} from '../../../../src/modules/knowledge_base/constants/record.constants'

describe('knowledge_base/constants/record.constants', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('RECORD_TYPE', () => {
    it('should have FILE', () => {
      expect(RECORD_TYPE.FILE).to.equal('FILE')
    })

    it('should have WEBPAGE', () => {
      expect(RECORD_TYPE.WEBPAGE).to.equal('WEBPAGE')
    })

    it('should have COMMENT', () => {
      expect(RECORD_TYPE.COMMENT).to.equal('COMMENT')
    })

    it('should have MESSAGE', () => {
      expect(RECORD_TYPE.MESSAGE).to.equal('MESSAGE')
    })

    it('should have EMAIL', () => {
      expect(RECORD_TYPE.EMAIL).to.equal('EMAIL')
    })

    it('should have OTHERS', () => {
      expect(RECORD_TYPE.OTHERS).to.equal('OTHERS')
    })

    it('should have exactly 6 record types', () => {
      expect(Object.keys(RECORD_TYPE)).to.have.lengthOf(6)
    })
  })

  describe('ORIGIN_TYPE', () => {
    it('should have UPLOAD', () => {
      expect(ORIGIN_TYPE.UPLOAD).to.equal('UPLOAD')
    })

    it('should have CONNECTOR', () => {
      expect(ORIGIN_TYPE.CONNECTOR).to.equal('CONNECTOR')
    })

    it('should have exactly 2 origin types', () => {
      expect(Object.keys(ORIGIN_TYPE)).to.have.lengthOf(2)
    })
  })

  describe('CONNECTOR_NAME', () => {
    it('should have all expected connectors', () => {
      expect(CONNECTOR_NAME.ONEDRIVE).to.equal('ONEDRIVE')
      expect(CONNECTOR_NAME.GOOGLE_DRIVE).to.equal('GOOGLE_DRIVE')
      expect(CONNECTOR_NAME.SHAREPOINT_ONLINE).to.equal('SHAREPOINT ONLINE')
      expect(CONNECTOR_NAME.GMAIL).to.equal('GMAIL')
      expect(CONNECTOR_NAME.CONFLUENCE).to.equal('CONFLUENCE')
      expect(CONNECTOR_NAME.JIRA).to.equal('JIRA')
      expect(CONNECTOR_NAME.SLACK).to.equal('SLACK')
    })

    it('should have exactly 7 connectors', () => {
      expect(Object.keys(CONNECTOR_NAME)).to.have.lengthOf(7)
    })
  })

  describe('INDEXING_STATUS', () => {
    it('should have all expected statuses', () => {
      expect(INDEXING_STATUS.NOT_STARTED).to.equal('NOT_STARTED')
      expect(INDEXING_STATUS.PAUSED).to.equal('PAUSED')
      expect(INDEXING_STATUS.IN_PROGRESS).to.equal('IN_PROGRESS')
      expect(INDEXING_STATUS.COMPLETED).to.equal('COMPLETED')
      expect(INDEXING_STATUS.FAILED).to.equal('FAILED')
      expect(INDEXING_STATUS.FILE_TYPE_NOT_SUPPORTED).to.equal('FILE_TYPE_NOT_SUPPORTED')
      expect(INDEXING_STATUS.AUTO_INDEX_OFF).to.equal('AUTO_INDEX_OFF')
      expect(INDEXING_STATUS.EMPTY).to.equal('EMPTY')
      expect(INDEXING_STATUS.ENABLE_MULTIMODAL_MODELS).to.equal('ENABLE_MULTIMODAL_MODELS')
      expect(INDEXING_STATUS.QUEUED).to.equal('QUEUED')
    })

    it('should have exactly 10 statuses', () => {
      expect(Object.keys(INDEXING_STATUS)).to.have.lengthOf(10)
    })
  })

  describe('ENTITY_TYPE', () => {
    it('should have KNOWLEDGE_BASE as "KB"', () => {
      expect(ENTITY_TYPE.KNOWLEDGE_BASE).to.equal('KB')
    })
  })

  describe('COLLECTIONS', () => {
    it('should have document collections', () => {
      expect(COLLECTIONS.RECORDS).to.equal('records')
      expect(COLLECTIONS.FILES).to.equal('files')
      expect(COLLECTIONS.USERS).to.equal('users')
      expect(COLLECTIONS.KNOWLEDGE_BASE).to.equal('knowledgeBase')
      expect(COLLECTIONS.BELONGS_TO_KNOWLEDGE_BASE).to.equal('belongsToKnowledgeBase')
      expect(COLLECTIONS.PERMISSIONS_TO_KNOWLEDGE_BASE).to.equal('permissionsToKnowledgeBase')
    })

    it('should have edge collections', () => {
      expect(COLLECTIONS.RECORD_TO_RECORD).to.equal('recordRelations')
      expect(COLLECTIONS.IS_OF_TYPE).to.equal('isOfType')
      expect(COLLECTIONS.PERMISSIONS).to.equal('permissions')
      expect(COLLECTIONS.BELONGS_TO).to.equal('belongsTo')
    })
  })

  describe('RELATIONSHIP_TYPE', () => {
    it('should have USER, GROUP, DOMAIN', () => {
      expect(RELATIONSHIP_TYPE.USER).to.equal('USER')
      expect(RELATIONSHIP_TYPE.GROUP).to.equal('GROUP')
      expect(RELATIONSHIP_TYPE.DOMAIN).to.equal('DOMAIN')
    })
  })

  describe('ROLE', () => {
    it('should have OWNER, WRITER, COMMENTER, READER', () => {
      expect(ROLE.OWNER).to.equal('OWNER')
      expect(ROLE.WRITER).to.equal('WRITER')
      expect(ROLE.COMMENTER).to.equal('COMMENTER')
      expect(ROLE.READER).to.equal('READER')
    })
  })

  describe('GRAPHS', () => {
    it('should have KB_GRAPH', () => {
      expect(GRAPHS.KB_GRAPH).to.equal('knowledgeBaseGraph')
    })
  })

  describe('COLLECTION_TYPE', () => {
    it('should have DOCUMENT as 2', () => {
      expect(COLLECTION_TYPE.DOCUMENT).to.equal(2)
    })

    it('should have EDGE as 3', () => {
      expect(COLLECTION_TYPE.EDGE).to.equal(3)
    })
  })

  describe('type guard: isValidRecordType', () => {
    it('should return true for valid record types', () => {
      expect(isValidRecordType('FILE')).to.be.true
      expect(isValidRecordType('WEBPAGE')).to.be.true
      expect(isValidRecordType('EMAIL')).to.be.true
    })

    it('should return false for invalid record types', () => {
      expect(isValidRecordType('INVALID')).to.be.false
      expect(isValidRecordType('')).to.be.false
    })
  })

  describe('type guard: isValidOriginType', () => {
    it('should return true for valid origin types', () => {
      expect(isValidOriginType('UPLOAD')).to.be.true
      expect(isValidOriginType('CONNECTOR')).to.be.true
    })

    it('should return false for invalid origin types', () => {
      expect(isValidOriginType('INVALID')).to.be.false
      expect(isValidOriginType('')).to.be.false
    })
  })

  describe('type guard: isValidConnectorName', () => {
    it('should return true for valid connector names', () => {
      expect(isValidConnectorName('ONEDRIVE')).to.be.true
      expect(isValidConnectorName('GOOGLE_DRIVE')).to.be.true
      expect(isValidConnectorName('SHAREPOINT ONLINE')).to.be.true
    })

    it('should return false for invalid connector names', () => {
      expect(isValidConnectorName('INVALID')).to.be.false
      expect(isValidConnectorName('')).to.be.false
    })
  })

  describe('type guard: isValidCollectionName', () => {
    it('should return true for valid collection names', () => {
      expect(isValidCollectionName('records')).to.be.true
      expect(isValidCollectionName('files')).to.be.true
      expect(isValidCollectionName('knowledgeBase')).to.be.true
    })

    it('should return false for invalid collection names', () => {
      expect(isValidCollectionName('INVALID')).to.be.false
      expect(isValidCollectionName('')).to.be.false
    })
  })
})
