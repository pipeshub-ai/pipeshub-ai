import 'reflect-metadata'
import { expect } from 'chai'
import {
  uploadRecordsSchema,
  uploadRecordsToFolderSchema,
  getAllRecordsSchema,
  getAllKBRecordsSchema,
  listKnowledgeBasesSchema,
  kbPermissionSchema,
  updatePermissionsSchema,
  reindexRecordSchema,
  reindexRecordGroupSchema,
  reindexFailedRecordSchema,
  resyncConnectorSchema,
  moveRecordSchema,
} from '../../../../src/modules/knowledge_base/validators/validators'

describe('Knowledge Base Validators - branch coverage', () => {

  // =========================================================================
  // uploadRecordsSchema - isVersioned string transform
  // =========================================================================
  describe('uploadRecordsSchema - isVersioned boolean/string', () => {
    it('should accept boolean true for isVersioned', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: true },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should accept boolean false for isVersioned', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: false },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should transform string "true" to boolean true', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'true' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.true
      }
    })

    it('should transform string "1" to boolean true', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '1' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.true
      }
    })

    it('should transform string "false" to boolean false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'false' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.false
      }
    })

    it('should transform string "0" to boolean false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '0' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should transform empty string to boolean false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })
  })

  // =========================================================================
  // uploadRecordsSchema - files_metadata validation
  // =========================================================================
  describe('uploadRecordsSchema - files_metadata', () => {
    it('should pass when files_metadata is not provided', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should pass for valid files_metadata JSON', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          files_metadata: JSON.stringify([{ file_path: 'test.pdf', last_modified: 123 }]),
        },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should fail for invalid JSON in files_metadata', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: 'not-json' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })

    it('should fail when files_metadata is not an array', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: JSON.stringify({ not: 'an array' }) },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })

    it('should fail when files_metadata entry has wrong types', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          files_metadata: JSON.stringify([{ file_path: 123, last_modified: 'not-number' }]),
        },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // getAllRecordsSchema - search XSS validation
  // =========================================================================
  describe('getAllRecordsSchema - search XSS validation', () => {
    it('should pass for normal search text', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: 'hello world' },
      })
      expect(result.success).to.be.true
    })

    it('should pass when search is not provided', () => {
      const result = getAllRecordsSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })

    it('should reject HTML tags in search', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: '<div>test</div>' },
      })
      expect(result.success).to.be.false
    })

    it('should reject script tags in search', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: '<script>alert("xss")</script>' },
      })
      expect(result.success).to.be.false
    })

    it('should reject closing script tags', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: '</script >' },
      })
      expect(result.success).to.be.false
    })

    it('should reject event handlers in search', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: 'onclick="alert(1)"' },
      })
      expect(result.success).to.be.false
    })

    it('should reject javascript: protocol in search', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: 'javascript:alert(1)' },
      })
      expect(result.success).to.be.false
    })

    it('should reject format specifiers in search', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: 'test %s injection' },
      })
      expect(result.success).to.be.false
    })

    it('should reject %1$s format specifier', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: '%1$s' },
      })
      expect(result.success).to.be.false
    })

    it('should reject %1!s format specifier', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: '%1!s' },
      })
      expect(result.success).to.be.false
    })

    it('should transform empty search to undefined', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { search: '   ' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.search).to.be.undefined
      }
    })
  })

  // =========================================================================
  // getAllRecordsSchema - pagination
  // =========================================================================
  describe('getAllRecordsSchema - pagination', () => {
    it('should reject page with value 0', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { page: '0' },
      })
      expect(result.success).to.be.false
    })

    it('should reject negative page', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { page: '-1' },
      })
      expect(result.success).to.be.false
    })

    it('should accept valid page', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { page: '1' },
      })
      expect(result.success).to.be.true
    })

    it('should reject limit over 100', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { limit: '101' },
      })
      expect(result.success).to.be.false
    })

    it('should accept limit of 100', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { limit: '100' },
      })
      expect(result.success).to.be.true
    })

    it('should reject non-numeric page', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { page: 'abc' },
      })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // getAllRecordsSchema - comma-separated transforms
  // =========================================================================
  describe('getAllRecordsSchema - comma-separated transforms', () => {
    it('should transform recordTypes CSV to array', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { recordTypes: 'FILE,WEBPAGE' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.recordTypes).to.deep.equal(['FILE', 'WEBPAGE'])
      }
    })

    it('should filter empty entries from recordTypes', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { recordTypes: 'FILE,,WEBPAGE,' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.recordTypes).to.deep.equal(['FILE', 'WEBPAGE'])
      }
    })

    it('should transform origins CSV to array', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { origins: 'UPLOAD,CONNECTOR' },
      })
      expect(result.success).to.be.true
    })

    it('should transform connectors CSV to array', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { connectors: 'google,slack' },
      })
      expect(result.success).to.be.true
    })

    it('should transform indexingStatus CSV to array', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { indexingStatus: 'INDEXED,PENDING' },
      })
      expect(result.success).to.be.true
    })

    it('should transform permissions CSV to array', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { permissions: 'read,write' },
      })
      expect(result.success).to.be.true
    })

    it('should return undefined when CSV fields are not provided', () => {
      const result = getAllRecordsSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.recordTypes).to.be.undefined
        expect(result.data.query.origins).to.be.undefined
        expect(result.data.query.connectors).to.be.undefined
      }
    })
  })

  // =========================================================================
  // getAllRecordsSchema - sortBy validation
  // =========================================================================
  describe('getAllRecordsSchema - sortBy', () => {
    it('should accept valid sort fields', () => {
      const validFields = ['createdAtTimestamp', 'updatedAtTimestamp', 'recordName', 'recordType', 'origin', 'indexingStatus']
      for (const field of validFields) {
        const result = getAllRecordsSchema.safeParse({ query: { sortBy: field } })
        expect(result.success, `sortBy=${field} should be valid`).to.be.true
      }
    })

    it('should reject invalid sort field', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { sortBy: 'invalidField' },
      })
      expect(result.success).to.be.false
    })

    it('should accept when sortBy is not provided', () => {
      const result = getAllRecordsSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })
  })

  // =========================================================================
  // getAllRecordsSchema - date validation
  // =========================================================================
  describe('getAllRecordsSchema - date validation', () => {
    it('should accept valid dateFrom', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { dateFrom: '1679000000' },
      })
      expect(result.success).to.be.true
    })

    it('should reject non-numeric dateFrom', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { dateFrom: 'not-a-date' },
      })
      expect(result.success).to.be.false
    })

    it('should reject negative dateFrom', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { dateFrom: '-1' },
      })
      expect(result.success).to.be.false
    })

    it('should accept valid dateTo', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { dateTo: '1679000000' },
      })
      expect(result.success).to.be.true
    })

    it('should reject non-numeric dateTo', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { dateTo: 'bad' },
      })
      expect(result.success).to.be.false
    })

    it('should pass when dates are not provided', () => {
      const result = getAllRecordsSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })
  })

  // =========================================================================
  // getAllRecordsSchema - source enum
  // =========================================================================
  describe('getAllRecordsSchema - source', () => {
    it('should accept "all"', () => {
      const result = getAllRecordsSchema.safeParse({ query: { source: 'all' } })
      expect(result.success).to.be.true
    })

    it('should accept "local"', () => {
      const result = getAllRecordsSchema.safeParse({ query: { source: 'local' } })
      expect(result.success).to.be.true
    })

    it('should accept "connector"', () => {
      const result = getAllRecordsSchema.safeParse({ query: { source: 'connector' } })
      expect(result.success).to.be.true
    })

    it('should reject invalid source', () => {
      const result = getAllRecordsSchema.safeParse({ query: { source: 'invalid' } })
      expect(result.success).to.be.false
    })

    it('should default to "all" when not provided', () => {
      const result = getAllRecordsSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.source).to.equal('all')
      }
    })
  })

  // =========================================================================
  // getAllRecordsSchema - strict mode
  // =========================================================================
  describe('getAllRecordsSchema - strict mode rejects unknown params', () => {
    it('should reject unknown query parameters', () => {
      const result = getAllRecordsSchema.safeParse({
        query: { unknownParam: 'value' },
      })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // kbPermissionSchema - refine validations
  // =========================================================================
  describe('kbPermissionSchema - refines', () => {
    it('should require at least one userId or teamId', () => {
      const result = kbPermissionSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })

    it('should pass with userIds and role', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: ['user1'], role: 'READER' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should pass with teamIds only (no role required)', () => {
      const result = kbPermissionSchema.safeParse({
        body: { teamIds: ['team1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should fail when userIds provided without role', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: ['user1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })

    it('should fail with empty userIds and empty teamIds', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: [], teamIds: [] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // updatePermissionsSchema - refine
  // =========================================================================
  describe('updatePermissionsSchema - refine', () => {
    it('should fail when teamIds are provided (teams cannot be updated)', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'READER', teamIds: ['team1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })

    it('should pass with userIds', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'WRITER', userIds: ['user1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })

    it('should pass with empty teamIds', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'OWNER', teamIds: [] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.true
    })
  })

  // =========================================================================
  // uploadRecordsToFolderSchema
  // =========================================================================
  describe('uploadRecordsToFolderSchema', () => {
    it('should require folderId in params', () => {
      const result = uploadRecordsToFolderSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      })
      expect(result.success).to.be.false
    })

    it('should pass with valid params', () => {
      const result = uploadRecordsToFolderSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000', folderId: 'folder-1' },
      })
      expect(result.success).to.be.true
    })
  })

  // =========================================================================
  // reindexRecordSchema - optional body
  // =========================================================================
  describe('reindexRecordSchema', () => {
    it('should pass with no body', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
      })
      expect(result.success).to.be.true
    })

    it('should pass with depth in body', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
        body: { depth: 5 },
      })
      expect(result.success).to.be.true
    })

    it('should reject depth below -1', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
        body: { depth: -2 },
      })
      expect(result.success).to.be.false
    })

    it('should reject depth above 100', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
        body: { depth: 101 },
      })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // moveRecordSchema
  // =========================================================================
  describe('moveRecordSchema', () => {
    it('should accept null newParentId', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: null },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000', recordId: 'rec1' },
      })
      expect(result.success).to.be.true
    })

    it('should accept string newParentId', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: 'folder-1' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000', recordId: 'rec1' },
      })
      expect(result.success).to.be.true
    })
  })

  // =========================================================================
  // listKnowledgeBasesSchema
  // =========================================================================
  describe('listKnowledgeBasesSchema - sortBy', () => {
    it('should accept name as sort field', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { sortBy: 'name' },
      })
      expect(result.success).to.be.true
    })

    it('should accept userRole as sort field', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { sortBy: 'userRole' },
      })
      expect(result.success).to.be.true
    })

    it('should reject invalid sort field', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { sortBy: 'invalidField' },
      })
      expect(result.success).to.be.false
    })

    it('should reject unknown query parameters (strict)', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { unknownField: 'value' },
      })
      expect(result.success).to.be.false
    })
  })
})
