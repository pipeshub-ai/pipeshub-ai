import 'reflect-metadata'
import { expect } from 'chai'
import {
  uploadRecordsSchema,
  uploadRecordsToFolderSchema,
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
