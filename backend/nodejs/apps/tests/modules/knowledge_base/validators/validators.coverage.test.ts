import 'reflect-metadata'
import { expect } from 'chai'
import {
  recordByIdSchema,
  updateRecordSchema,
  deleteRecordSchema,
  reindexRecordSchema,
  reindexRecordGroupSchema,
  reindexFailedRecordSchema,
  resyncSchema,
  connectorStatsSchema,
  uploadRecordsSchema,
  uploadRecordsToFolderSchema,
  allRecordsSchema,
  createSchema,
  kbIdParamSchema,
  listSchema,
  updateSchema,
  deleteSchema,
  createFolderSchema,
  permissionBodySchema,
  getFolderSchema,
  updateFolderSchema,
  deleteFolderSchema,
  getPermissionsSchema,
  updatePermissionsSchema,
  deletePermissionsSchema,
  moveRecordSchema,
} from '../../../../src/modules/knowledge_base/schemas/knowledge_base'

describe('Knowledge Base Validators - coverage', () => {
  // -----------------------------------------------------------------------
  // uploadRecordsSchema
  // -----------------------------------------------------------------------
  describe('uploadRecordsSchema', () => {
    it('should accept valid upload with UUID kbId', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { recordName: 'test', recordType: 'FILE', origin: 'UPLOAD', isVersioned: false },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('should accept isVersioned as string true', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'true' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.true
      }
    })

    it('should accept isVersioned as string false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'false' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.false
      }
    })

    it('should accept isVersioned as string 0', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '0' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('should accept isVersioned as string 1', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '1' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('should accept isVersioned as empty string', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('should accept valid files_metadata JSON', () => {
      const metadata = JSON.stringify([{ file_path: '/test.pdf', last_modified: 12345 }])
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: metadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('should reject invalid files_metadata JSON', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: 'not-json' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.false
    })

    it('should reject files_metadata with missing fields', () => {
      const metadata = JSON.stringify([{ file_path: '/test.pdf' }]) // missing last_modified
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: metadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.false
    })

    it('should reject files_metadata that is not an array', () => {
      const metadata = JSON.stringify({ file_path: '/test.pdf', last_modified: 123 })
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: metadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.false
    })

    it('should reject non-UUID kbId', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: 'not-a-uuid' },
      })
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // uploadRecordsToFolderSchema
  // -----------------------------------------------------------------------
  describe('uploadRecordsToFolderSchema', () => {
    it('should require folderId param', () => {
      const result = uploadRecordsToFolderSchema.safeParse({
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.false
    })

    it('should accept valid params', () => {
      const result = uploadRecordsToFolderSchema.safeParse({
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', folderId: 'folder-123' },
      })
      expect(result.success).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // allRecordsSchema
  // -----------------------------------------------------------------------
  describe('allRecordsSchema', () => {
    it('should accept valid query params', () => {
      const result = allRecordsSchema.safeParse({
        query: { page: '1', limit: '20', search: 'test' },
      })
      expect(result.success).to.be.true
    })

    it('should reject script tags in search', () => {
      const result = allRecordsSchema.safeParse({
        query: { search: '<script>xss</script>' },
      })
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // permissionBodySchema
  // -----------------------------------------------------------------------
  describe('permissionBodySchema', () => {
    it('should accept valid user permission', () => {
      const result = permissionBodySchema.safeParse({
        body: { userIds: ['user1'], role: 'READER' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('should accept valid team permission', () => {
      const result = permissionBodySchema.safeParse({
        body: { teamIds: ['team1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('should reject when no userIds or teamIds', () => {
      const result = permissionBodySchema.safeParse({
        body: { role: 'READER' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.false
    })

    it('should reject when userIds provided without role', () => {
      const result = permissionBodySchema.safeParse({
        body: { userIds: ['user1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // updatePermissionsSchema
  // -----------------------------------------------------------------------
  describe('updatePermissionsSchema', () => {
    it('should reject teamIds in update', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'WRITER', teamIds: ['team1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.false
    })

    it('should accept userIds with role', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'WRITER', userIds: ['user1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // listSchema
  // -----------------------------------------------------------------------
  describe('listSchema', () => {
    it('should accept valid query params', () => {
      const result = listSchema.safeParse({
        query: { page: '1', limit: '20', sortBy: 'name', sortOrder: 'asc' },
      })
      expect(result.success).to.be.true
    })

    it('should reject unknown query params (strict mode)', () => {
      const result = listSchema.safeParse({
        query: { unknownParam: 'value' },
      })
      expect(result.success).to.be.false
    })

    it('should accept search with permissions filter', () => {
      const result = listSchema.safeParse({
        query: { permissions: 'OWNER,READER' },
      })
      expect(result.success).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // moveRecordSchema
  // -----------------------------------------------------------------------
  describe('moveRecordSchema', () => {
    it('should accept valid move request', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: 'folder-123' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', recordId: 'rec-1' },
      })
      expect(result.success).to.be.true
    })

    it('should accept null newParentId (move to root)', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: null },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', recordId: 'rec-1' },
      })
      expect(result.success).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Simple schemas
  // -----------------------------------------------------------------------
  describe('simple schemas', () => {
    it('recordByIdSchema should accept valid input', () => {
      const result = recordByIdSchema.safeParse({
        params: { recordId: 'rec-1' },
        query: { convertTo: 'pdf' },
      })
      expect(result.success).to.be.true
    })

    it('reindexRecordSchema should accept depth', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec-1' },
        body: { depth: 5 },
      })
      expect(result.success).to.be.true
    })

    it('reindexRecordGroupSchema should accept depth', () => {
      const result = reindexRecordGroupSchema.safeParse({
        params: { recordGroupId: 'grp-1' },
        body: { depth: -1 },
      })
      expect(result.success).to.be.true
    })

    it('reindexFailedRecordSchema should accept valid input', () => {
      const result = reindexFailedRecordSchema.safeParse({
        body: { app: 'google', connectorId: 'c-1', statusFilters: ['FAILED'] },
      })
      expect(result.success).to.be.true
    })

    it('resyncSchema should accept valid input', () => {
      const result = resyncSchema.safeParse({
        body: { connectorName: 'google', connectorId: 'c-1', fullSync: true },
      })
      expect(result.success).to.be.true
    })

    it('createSchema should accept valid input', () => {
      const result = createSchema.safeParse({
        body: { kbName: 'My KB' },
      })
      expect(result.success).to.be.true
    })

    it('updateSchema should accept valid input', () => {
      const result = updateSchema.safeParse({
        body: { kbName: 'Updated KB' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })

    it('createFolderSchema should accept valid input', () => {
      const result = createFolderSchema.safeParse({
        body: { folderName: 'New Folder' },
      })
      expect(result.success).to.be.true
    })

    it('deletePermissionsSchema should accept valid input', () => {
      const result = deletePermissionsSchema.safeParse({
        body: { userIds: ['u1'], teamIds: ['t1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      })
      expect(result.success).to.be.true
    })
  })
})
