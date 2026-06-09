import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  getRecordByIdSchema,
  updateRecordSchema,
  deleteRecordSchema,
  reindexRecordSchema,
  reindexRecordGroupSchema,
  reindexFailedRecordSchema,
  resyncConnectorSchema,
  getConnectorStatsSchema,
  uploadRecordsSchema,
  uploadRecordsToFolderSchema,
  getAllKBRecordsSchema,
  createKBSchema,
  getKBSchema,
  listKnowledgeBasesSchema,
  updateKBSchema,
  deleteKBSchema,
  createFolderSchema,
  kbPermissionSchema,
  getFolderSchema,
  updateFolderSchema,
  deleteFolderSchema,
  getPermissionsSchema,
  updatePermissionsSchema,
  deletePermissionsSchema,
  moveRecordSchema,
} from '../../../../src/modules/knowledge_base/validators/validators'

describe('knowledge_base/validators/validators', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getRecordByIdSchema', () => {
    it('should accept valid recordId', () => {
      const data = { params: { recordId: 'rec-123' }, query: {} }
      const result = getRecordByIdSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty recordId', () => {
      const data = { params: { recordId: '' }, query: {} }
      const result = getRecordByIdSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept optional convertTo query param', () => {
      const data = { params: { recordId: 'rec-123' }, query: { convertTo: 'pdf' } }
      const result = getRecordByIdSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('updateRecordSchema', () => {
    it('should accept valid update data', () => {
      const data = {
        body: { recordName: 'Updated Record' },
        params: { recordId: 'rec-123' },
      }
      const result = updateRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept with optional fileBuffer', () => {
      const data = {
        body: { fileBuffer: Buffer.from('test'), recordName: 'test' },
        params: { recordId: 'rec-123' },
      }
      const result = updateRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('deleteRecordSchema', () => {
    it('should accept valid recordId', () => {
      const data = { params: { recordId: 'rec-123' } }
      const result = deleteRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty recordId', () => {
      const data = { params: { recordId: '' } }
      const result = deleteRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('reindexRecordSchema', () => {
    it('should accept valid reindex request', () => {
      const data = { params: { recordId: 'rec-123' } }
      const result = reindexRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept optional depth', () => {
      const data = { params: { recordId: 'rec-123' }, body: { depth: 5 } }
      const result = reindexRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('reindexRecordGroupSchema', () => {
    it('should accept valid recordGroupId', () => {
      const data = { params: { recordGroupId: 'rg-123' } }
      const result = reindexRecordGroupSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('reindexFailedRecordSchema', () => {
    it('should accept valid failed reindex request', () => {
      const data = { body: { app: 'drive', connectorId: 'conn-1' } }
      const result = reindexFailedRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing app', () => {
      const data = { body: { connectorId: 'conn-1' } }
      const result = reindexFailedRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('resyncConnectorSchema', () => {
    it('should accept valid resync request', () => {
      const data = { body: { connectorName: 'drive', connectorId: 'conn-1' } }
      const result = resyncConnectorSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept optional fullSync', () => {
      const data = { body: { connectorName: 'drive', connectorId: 'conn-1', fullSync: true } }
      const result = resyncConnectorSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('getConnectorStatsSchema', () => {
    it('should accept valid connectorId', () => {
      const data = { params: { connectorId: 'conn-1' } }
      const result = getConnectorStatsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty connectorId', () => {
      const data = { params: { connectorId: '' } }
      const result = getConnectorStatsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Upload Records Schema
  // -----------------------------------------------------------------------
  describe('uploadRecordsSchema', () => {
    it('should accept valid upload data with UUID kbId', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = uploadRecordsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject non-UUID kbId', () => {
      const data = {
        body: {},
        params: { kbId: 'not-a-uuid' },
      }
      const result = uploadRecordsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept isVersioned as boolean', () => {
      const data = {
        body: { isVersioned: true },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = uploadRecordsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should transform isVersioned string "true" to boolean', () => {
      const data = {
        body: { isVersioned: 'true' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = uploadRecordsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept valid files_metadata JSON', () => {
      const filesMetadata = JSON.stringify([{ file_path: '/uploads/test.pdf', last_modified: 1234567890 }])
      const data = {
        body: { files_metadata: filesMetadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = uploadRecordsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid files_metadata JSON', () => {
      const data = {
        body: { files_metadata: 'not-json' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = uploadRecordsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject files_metadata with missing fields', () => {
      const filesMetadata = JSON.stringify([{ file_path: '/test.pdf' }])
      const data = {
        body: { files_metadata: filesMetadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = uploadRecordsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('uploadRecordsToFolderSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', folderId: 'folder-1' },
      }
      const result = uploadRecordsToFolderSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing folderId', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = uploadRecordsToFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject empty folderId', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', folderId: '' },
      }
      const result = uploadRecordsToFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // KB CRUD Schemas
  // -----------------------------------------------------------------------
  describe('createKBSchema', () => {
    it('should accept valid kbName', () => {
      const data = { body: { kbName: 'My KB' } }
      const result = createKBSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty kbName', () => {
      const data = { body: { kbName: '' } }
      const result = createKBSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject kbName over 255 characters', () => {
      const data = { body: { kbName: 'a'.repeat(256) } }
      const result = createKBSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('getKBSchema', () => {
    it('should accept valid kbId', () => {
      const data = { params: { kbId: 'kb-123' } }
      const result = getKBSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '' } }
      const result = getKBSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('updateKBSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { kbName: 'Updated' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = updateKBSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject non-UUID kbId', () => {
      const data = {
        body: { kbName: 'Updated' },
        params: { kbId: 'not-uuid' },
      }
      const result = updateKBSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('deleteKBSchema', () => {
    it('should accept valid kbId', () => {
      const data = { params: { kbId: 'kb-123' } }
      const result = deleteKBSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '' } }
      const result = deleteKBSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Folder Schemas
  // -----------------------------------------------------------------------
  describe('createFolderSchema', () => {
    it('should accept valid folderName', () => {
      const data = { body: { folderName: 'My Folder' } }
      const result = createFolderSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty folderName', () => {
      const data = { body: { folderName: '' } }
      const result = createFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject folderName over 255 characters', () => {
      const data = { body: { folderName: 'x'.repeat(256) } }
      const result = createFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('getFolderSchema', () => {
    it('should accept valid params', () => {
      const data = { params: { kbId: 'kb-1', folderId: 'f-1' } }
      const result = getFolderSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '', folderId: 'f-1' } }
      const result = getFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject empty folderId', () => {
      const data = { params: { kbId: 'kb-1', folderId: '' } }
      const result = getFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('updateFolderSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { folderName: 'Updated Folder' },
        params: { kbId: 'kb-1', folderId: 'f-1' },
      }
      const result = updateFolderSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty folderName', () => {
      const data = {
        body: { folderName: '' },
        params: { kbId: 'kb-1', folderId: 'f-1' },
      }
      const result = updateFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('deleteFolderSchema', () => {
    it('should accept valid params', () => {
      const data = { params: { kbId: 'kb-1', folderId: 'f-1' } }
      const result = deleteFolderSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '', folderId: 'f-1' } }
      const result = deleteFolderSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Permission Schemas
  // -----------------------------------------------------------------------
  describe('kbPermissionSchema', () => {
    it('should accept valid user permission', () => {
      const data = {
        body: { userIds: ['user-1'], teamIds: [], role: 'WRITER' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = kbPermissionSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept valid team permission', () => {
      const data = {
        body: { userIds: [], teamIds: ['team-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = kbPermissionSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject when both userIds and teamIds are empty', () => {
      const data = {
        body: { userIds: [], teamIds: [] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = kbPermissionSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject when role is missing for users', () => {
      const data = {
        body: { userIds: ['user-1'], teamIds: [] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = kbPermissionSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject invalid role', () => {
      const data = {
        body: { userIds: ['user-1'], teamIds: [], role: 'ADMIN' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = kbPermissionSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('getPermissionsSchema', () => {
    it('should accept valid kbId', () => {
      const data = { params: { kbId: 'kb-1' } }
      const result = getPermissionsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '' } }
      const result = getPermissionsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('updatePermissionsSchema', () => {
    it('should accept valid user permission update', () => {
      const data = {
        body: { role: 'READER', userIds: ['user-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = updatePermissionsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject team updates', () => {
      const data = {
        body: { role: 'READER', teamIds: ['team-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = updatePermissionsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject invalid role', () => {
      const data = {
        body: { role: 'ADMIN', userIds: ['user-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = updatePermissionsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('deletePermissionsSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { userIds: ['user-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      }
      const result = deletePermissionsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject non-UUID kbId', () => {
      const data = {
        body: { userIds: ['user-1'] },
        params: { kbId: 'not-uuid' },
      }
      const result = deletePermissionsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Move Record Schema
  // -----------------------------------------------------------------------
  describe('moveRecordSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { newParentId: 'folder-123' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', recordId: 'rec-1' },
      }
      const result = moveRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept null newParentId (move to root)', () => {
      const data = {
        body: { newParentId: null },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', recordId: 'rec-1' },
      }
      const result = moveRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty recordId', () => {
      const data = {
        body: { newParentId: null },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', recordId: '' },
      }
      const result = moveRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject non-UUID kbId', () => {
      const data = {
        body: { newParentId: null },
        params: { kbId: 'not-uuid', recordId: 'rec-1' },
      }
      const result = moveRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // listKnowledgeBasesSchema
  // -----------------------------------------------------------------------
  describe('listKnowledgeBasesSchema', () => {
    it('should accept empty query', () => {
      const data = { query: {} }
      const result = listKnowledgeBasesSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject XSS in search', () => {
      const data = { query: { search: '<script>alert(1)</script>' } }
      const result = listKnowledgeBasesSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid sortBy', () => {
      const data = { query: { sortBy: 'name' } }
      const result = listKnowledgeBasesSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid sortBy', () => {
      const data = { query: { sortBy: 'invalidField' } }
      const result = listKnowledgeBasesSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject unknown query parameters', () => {
      const data = { query: { unknown: 'val' } }
      const result = listKnowledgeBasesSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject page 0', () => {
      const data = { query: { page: '0' } }
      const result = listKnowledgeBasesSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Reindex schemas - additional tests
  // -----------------------------------------------------------------------
  describe('reindexRecordSchema (additional)', () => {
    it('should reject empty recordId', () => {
      const data = { params: { recordId: '' } }
      const result = reindexRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject depth below -1', () => {
      const data = { params: { recordId: 'rec-1' }, body: { depth: -2 } }
      const result = reindexRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject depth above 100', () => {
      const data = { params: { recordId: 'rec-1' }, body: { depth: 101 } }
      const result = reindexRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept depth of -1', () => {
      const data = { params: { recordId: 'rec-1' }, body: { depth: -1 } }
      const result = reindexRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('reindexRecordGroupSchema (additional)', () => {
    it('should reject empty recordGroupId', () => {
      const data = { params: { recordGroupId: '' } }
      const result = reindexRecordGroupSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('reindexFailedRecordSchema (additional)', () => {
    it('should reject missing connectorId', () => {
      const data = { body: { app: 'drive' } }
      const result = reindexFailedRecordSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept optional statusFilters', () => {
      const data = { body: { app: 'drive', connectorId: 'conn-1', statusFilters: ['FAILED', 'ERROR'] } }
      const result = reindexFailedRecordSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('resyncConnectorSchema (additional)', () => {
    it('should reject missing connectorName', () => {
      const data = { body: { connectorId: 'conn-1' } }
      const result = resyncConnectorSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject missing connectorId', () => {
      const data = { body: { connectorName: 'drive' } }
      const result = resyncConnectorSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })
})
