import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  getRecordByIdSchema,
  updateRecordSchema,
  deleteRecordSchema,
  reindexRecordSchema,
  reindexRecordGroupSchema,
  uploadRecordsSchema,
  createKBSchema,
  getKBSchema,
  listKnowledgeBasesSchema,
  updateKBSchema,
  deleteKBSchema,
  createFolderSchema,
  kbPermissionSchema,
  updateFolderSchema,
  deleteFolderSchema,
  getPermissionsSchema,
  updatePermissionsSchema,
  deletePermissionsSchema,
  moveRecordSchema,
} from '../../../../src/modules/knowledge_base/validators/validators';
import { FileRejectionReason } from '../../../../src/libs/middlewares/file_processor/fp.constant';

describe('knowledge_base/validators/validators', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('getRecordByIdSchema', () => {
    it('should accept valid recordId', () => {
      const data = { params: { recordId: 'rec-123' }, query: {} };
      const result = getRecordByIdSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty recordId', () => {
      const data = { params: { recordId: '' }, query: {} };
      const result = getRecordByIdSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should accept optional convertTo query param', () => {
      const data = {
        params: { recordId: 'rec-123' },
        query: { convertTo: 'pdf' },
      };
      const result = getRecordByIdSchema.safeParse(data);
      expect(result.success).to.be.true;
    });
  });

  describe('updateRecordSchema', () => {
    it('should accept valid update data', () => {
      const data = {
        body: { recordName: 'Updated Record' },
        params: { recordId: 'rec-123' },
      };
      const result = updateRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should accept with optional fileBuffer', () => {
      const data = {
        body: { fileBuffer: Buffer.from('test'), recordName: 'test' },
        params: { recordId: 'rec-123' },
      };
      const result = updateRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });
  });

  describe('deleteRecordSchema', () => {
    it('should accept valid recordId', () => {
      const data = { params: { recordId: 'rec-123' } };
      const result = deleteRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty recordId', () => {
      const data = { params: { recordId: '' } };
      const result = deleteRecordSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('reindexRecordSchema', () => {
    it('should accept valid reindex request', () => {
      const data = { params: { recordId: 'rec-123' } };
      const result = reindexRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should accept optional depth', () => {
      const data = { params: { recordId: 'rec-123' }, body: { depth: 5 } };
      const result = reindexRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });
  });

  describe('reindexRecordGroupSchema', () => {
    it('should accept valid recordGroupId', () => {
      const data = { params: { recordGroupId: 'rg-123' } };
      const result = reindexRecordGroupSchema.safeParse(data);
      expect(result.success).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Upload Records Schema
  // -----------------------------------------------------------------------
  describe('uploadRecordsSchema', () => {
    it('should accept valid upload data with UUID kbId', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should default isVersioned to true when omitted', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.true;
      }
    });

    it('should reject non-UUID kbId', () => {
      const data = {
        body: {},
        params: { kbId: 'not-a-uuid' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should accept isVersioned as boolean', () => {
      const data = {
        body: { isVersioned: true },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should transform isVersioned string "true" to boolean', () => {
      const data = {
        body: { isVersioned: 'true' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should accept valid files_metadata JSON', () => {
      const filesMetadata = JSON.stringify([
        { file_path: '/uploads/test.pdf', last_modified: 1234567890 },
      ]);
      const data = {
        body: { files_metadata: filesMetadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject invalid files_metadata JSON', () => {
      const data = {
        body: { files_metadata: 'not-json' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject files_metadata with missing fields', () => {
      const filesMetadata = JSON.stringify([{ file_path: '/test.pdf' }]);
      const data = {
        body: { files_metadata: filesMetadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
    it('should accept optional folderId query param', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
        query: { folderId: 'folder-1' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should accept upload without folderId (KB root)', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty folderId query param', () => {
      const data = {
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
        query: { folderId: '' },
      };
      const result = uploadRecordsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // KB CRUD Schemas
  // -----------------------------------------------------------------------
  describe('createKBSchema', () => {
    it('should accept valid kbName', () => {
      const data = { body: { kbName: 'My KB' } };
      const result = createKBSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty kbName', () => {
      const data = { body: { kbName: '' } };
      const result = createKBSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject kbName over 255 characters', () => {
      const data = { body: { kbName: 'a'.repeat(256) } };
      const result = createKBSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('getKBSchema', () => {
    it('should accept valid kbId', () => {
      const data = { params: { kbId: 'kb-123' } };
      const result = getKBSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '' } };
      const result = getKBSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('updateKBSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { kbName: 'Updated' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = updateKBSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject non-UUID kbId', () => {
      const data = {
        body: { kbName: 'Updated' },
        params: { kbId: 'not-uuid' },
      };
      const result = updateKBSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('deleteKBSchema', () => {
    it('should accept valid kbId', () => {
      const data = { params: { kbId: 'kb-123' } };
      const result = deleteKBSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '' } };
      const result = deleteKBSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // Folder Schemas
  // -----------------------------------------------------------------------
  describe('createFolderSchema', () => {
    const validKbId = '550e8400-e29b-41d4-a716-446655440000';

    it('should accept valid root folder input', () => {
      const data = {
        body: { folderName: 'My Folder' },
        params: { kbId: validKbId },
        query: {},
      };
      const result = createFolderSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should accept valid nested folder input with folderId query', () => {
      const data = {
        body: { folderName: 'My Subfolder' },
        params: { kbId: validKbId },
        query: { folderId: 'parent-folder-1' },
      };
      const result = createFolderSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject invalid kbId', () => {
      const data = {
        body: { folderName: 'My Folder' },
        params: { kbId: 'not-a-uuid' },
        query: {},
      };
      const result = createFolderSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject empty folderName', () => {
      const data = {
        body: { folderName: '' },
        params: { kbId: validKbId },
        query: {},
      };
      const result = createFolderSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject folderName over 255 characters', () => {
      const data = {
        body: { folderName: 'x'.repeat(256) },
        params: { kbId: validKbId },
        query: {},
      };
      const result = createFolderSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('updateFolderSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { folderName: 'Updated Folder' },
        params: { kbId: 'kb-1', folderId: 'f-1' },
      };
      const result = updateFolderSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty folderName', () => {
      const data = {
        body: { folderName: '' },
        params: { kbId: 'kb-1', folderId: 'f-1' },
      };
      const result = updateFolderSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('deleteFolderSchema', () => {
    it('should accept valid params', () => {
      const data = { params: { kbId: 'kb-1', folderId: 'f-1' } };
      const result = deleteFolderSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '', folderId: 'f-1' } };
      const result = deleteFolderSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // Permission Schemas
  // -----------------------------------------------------------------------
  describe('kbPermissionSchema', () => {
    it('should accept valid user permission', () => {
      const data = {
        body: { userIds: ['user-1'], teamIds: [], role: 'WRITER' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = kbPermissionSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should accept valid team permission', () => {
      const data = {
        body: { userIds: [], teamIds: ['team-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = kbPermissionSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject when both userIds and teamIds are empty', () => {
      const data = {
        body: { userIds: [], teamIds: [] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = kbPermissionSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject when role is missing for users', () => {
      const data = {
        body: { userIds: ['user-1'], teamIds: [] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = kbPermissionSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject invalid role', () => {
      const data = {
        body: { userIds: ['user-1'], teamIds: [], role: 'ADMIN' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = kbPermissionSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('getPermissionsSchema', () => {
    it('should accept valid kbId', () => {
      const data = { params: { kbId: 'kb-1' } };
      const result = getPermissionsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty kbId', () => {
      const data = { params: { kbId: '' } };
      const result = getPermissionsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('updatePermissionsSchema', () => {
    it('should accept valid user permission update', () => {
      const data = {
        body: { role: 'READER', userIds: ['user-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = updatePermissionsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject team updates', () => {
      const data = {
        body: { role: 'READER', teamIds: ['team-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = updatePermissionsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject invalid role', () => {
      const data = {
        body: { role: 'ADMIN', userIds: ['user-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = updatePermissionsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  describe('deletePermissionsSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { userIds: ['user-1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      };
      const result = deletePermissionsSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject non-UUID kbId', () => {
      const data = {
        body: { userIds: ['user-1'] },
        params: { kbId: 'not-uuid' },
      };
      const result = deletePermissionsSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // Move Record Schema
  // -----------------------------------------------------------------------
  describe('moveRecordSchema', () => {
    it('should accept valid data', () => {
      const data = {
        body: { newParentId: 'folder-123' },
        params: {
          kbId: '550e8400-e29b-41d4-a716-446655440000',
          recordId: 'rec-1',
        },
      };
      const result = moveRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should accept null newParentId (move to root)', () => {
      const data = {
        body: { newParentId: null },
        params: {
          kbId: '550e8400-e29b-41d4-a716-446655440000',
          recordId: 'rec-1',
        },
      };
      const result = moveRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject empty recordId', () => {
      const data = {
        body: { newParentId: null },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000', recordId: '' },
      };
      const result = moveRecordSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject non-UUID kbId', () => {
      const data = {
        body: { newParentId: null },
        params: { kbId: 'not-uuid', recordId: 'rec-1' },
      };
      const result = moveRecordSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // listKnowledgeBasesSchema
  // -----------------------------------------------------------------------
  describe('listKnowledgeBasesSchema', () => {
    it('should accept empty query', () => {
      const data = { query: {} };
      const result = listKnowledgeBasesSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject XSS in search', () => {
      const data = { query: { search: '<script>alert(1)</script>' } };
      const result = listKnowledgeBasesSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should accept valid sortBy', () => {
      const data = { query: { sortBy: 'name' } };
      const result = listKnowledgeBasesSchema.safeParse(data);
      expect(result.success).to.be.true;
    });

    it('should reject invalid sortBy', () => {
      const data = { query: { sortBy: 'invalidField' } };
      const result = listKnowledgeBasesSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject unknown query parameters', () => {
      const data = { query: { unknown: 'val' } };
      const result = listKnowledgeBasesSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject page 0', () => {
      const data = { query: { page: '0' } };
      const result = listKnowledgeBasesSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // Reindex schemas - additional tests
  // -----------------------------------------------------------------------
  describe('reindexRecordSchema (additional)', () => {
    it('should reject empty recordId', () => {
      const data = { params: { recordId: '' } };
      const result = reindexRecordSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject depth below -1', () => {
      const data = { params: { recordId: 'rec-1' }, body: { depth: -2 } };
      const result = reindexRecordSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should reject depth above 100', () => {
      const data = { params: { recordId: 'rec-1' }, body: { depth: 101 } };
      const result = reindexRecordSchema.safeParse(data);
      expect(result.success).to.be.false;
    });

    it('should accept depth of -1', () => {
      const data = { params: { recordId: 'rec-1' }, body: { depth: -1 } };
      const result = reindexRecordSchema.safeParse(data);
      expect(result.success).to.be.true;
    });
  });

  describe('reindexRecordGroupSchema (additional)', () => {
    it('should reject empty recordGroupId', () => {
      const data = { params: { recordGroupId: '' } };
      const result = reindexRecordGroupSchema.safeParse(data);
      expect(result.success).to.be.false;
    });
  });
});

describe('Knowledge Base Validators - branch coverage', () => {
  // =========================================================================
  // uploadRecordsSchema - isVersioned string transform
  // =========================================================================
  describe('uploadRecordsSchema - isVersioned boolean/string', () => {
    it('should accept boolean true for isVersioned', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: true },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept boolean false for isVersioned', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: false },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should transform string "true" to boolean true', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'true' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.true;
      }
    });

    it('should transform string "1" to boolean true', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '1' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.true;
      }
    });

    it('should transform string "false" to boolean false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'false' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.false;
      }
    });

    it('should transform string "0" to boolean false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '0' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should transform empty string to boolean false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });
  });

  // =========================================================================
  // uploadRecordsSchema - files_metadata validation
  // =========================================================================
  describe('uploadRecordsSchema - files_metadata', () => {
    it('should pass when files_metadata is not provided', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should pass for valid files_metadata JSON', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          files_metadata: JSON.stringify([
            { file_path: 'test.pdf', last_modified: 123 },
          ]),
        },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should fail for invalid JSON in files_metadata', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: 'not-json' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.false;
    });

    it('should fail when files_metadata is not an array', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: JSON.stringify({ not: 'an array' }) },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.false;
    });

    it('should fail when files_metadata entry has wrong types', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          files_metadata: JSON.stringify([
            { file_path: 123, last_modified: 'not-number' },
          ]),
        },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.false;
    });
  });

  // =========================================================================
  // kbPermissionSchema - refine validations
  // =========================================================================
  describe('kbPermissionSchema - refines', () => {
    it('should require at least one userId or teamId', () => {
      const result = kbPermissionSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.false;
    });

    it('should pass with userIds and role', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: ['user1'], role: 'READER' },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should pass with teamIds only (no role required)', () => {
      const result = kbPermissionSchema.safeParse({
        body: { teamIds: ['team1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should fail when userIds provided without role', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: ['user1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.false;
    });

    it('should fail with empty userIds and empty teamIds', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: [], teamIds: [] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.false;
    });
  });

  // =========================================================================
  // updatePermissionsSchema - refine
  // =========================================================================
  describe('updatePermissionsSchema - refine', () => {
    it('should fail when teamIds are provided (teams cannot be updated)', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'READER', teamIds: ['team1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.false;
    });

    it('should pass with userIds', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'WRITER', userIds: ['user1'] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });

    it('should pass with empty teamIds', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'OWNER', teamIds: [] },
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
      });
      expect(result.success).to.be.true;
    });
  });

  // =========================================================================
  // uploadRecordsSchema — folderId query
  // =========================================================================
  describe('uploadRecordsSchema - folderId query', () => {
    it('should reject empty folderId query', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
        query: { folderId: '' },
      });
      expect(result.success).to.be.false;
    });

    it('should pass with valid folderId query', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: '123e4567-e89b-12d3-a456-426614174000' },
        query: { folderId: 'folder-1' },
      });
      expect(result.success).to.be.true;
    });
  });

  // =========================================================================
  // reindexRecordSchema - optional body
  // =========================================================================
  describe('reindexRecordSchema', () => {
    it('should pass with no body', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
      });
      expect(result.success).to.be.true;
    });

    it('should pass with depth in body', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
        body: { depth: 5 },
      });
      expect(result.success).to.be.true;
    });

    it('should reject depth below -1', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
        body: { depth: -2 },
      });
      expect(result.success).to.be.false;
    });

    it('should reject depth above 100', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec1' },
        body: { depth: 101 },
      });
      expect(result.success).to.be.false;
    });
  });

  // =========================================================================
  // moveRecordSchema
  // =========================================================================
  describe('moveRecordSchema', () => {
    it('should accept null newParentId', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: null },
        params: {
          kbId: '123e4567-e89b-12d3-a456-426614174000',
          recordId: 'rec1',
        },
      });
      expect(result.success).to.be.true;
    });

    it('should accept string newParentId', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: 'folder-1' },
        params: {
          kbId: '123e4567-e89b-12d3-a456-426614174000',
          recordId: 'rec1',
        },
      });
      expect(result.success).to.be.true;
    });
  });

  // =========================================================================
  // listKnowledgeBasesSchema
  // =========================================================================
  describe('listKnowledgeBasesSchema - sortBy', () => {
    it('should accept name as sort field', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { sortBy: 'name' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept userRole as sort field', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { sortBy: 'userRole' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject invalid sort field', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { sortBy: 'invalidField' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject unknown query parameters (strict)', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { unknownField: 'value' },
      });
      expect(result.success).to.be.false;
    });
  });
});

describe('Knowledge Base Validators - coverage', () => {
  // -----------------------------------------------------------------------
  // uploadRecordsSchema
  // -----------------------------------------------------------------------
  describe('uploadRecordsSchema', () => {
    it('should accept valid upload with UUID kbId', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          recordName: 'test',
          recordType: 'FILE',
          origin: 'UPLOAD',
          isVersioned: false,
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept isVersioned as string true', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'true' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.true;
      }
    });

    it('should accept isVersioned as string false', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: 'false' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
      if (result.success) {
        expect(result.data.body.isVersioned).to.be.false;
      }
    });

    it('should accept isVersioned as string 0', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '0' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept isVersioned as string 1', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '1' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept isVersioned as empty string', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { isVersioned: '' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept valid files_metadata JSON', () => {
      const metadata = JSON.stringify([
        { file_path: '/test.pdf', last_modified: 12345 },
      ]);
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: metadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject invalid files_metadata JSON', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: 'not-json' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject files_metadata with missing fields', () => {
      const metadata = JSON.stringify([{ file_path: '/test.pdf' }]); // missing last_modified
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: metadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject files_metadata that is not an array', () => {
      const metadata = JSON.stringify({
        file_path: '/test.pdf',
        last_modified: 123,
      });
      const result = uploadRecordsSchema.safeParse({
        body: { files_metadata: metadata },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject non-UUID kbId', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: 'not-a-uuid' },
      });
      expect(result.success).to.be.false;
    });
    it('should accept optional folderId query param', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
        query: { folderId: 'folder-123' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject empty folderId query param', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
        query: { folderId: '' },
      });
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // kbPermissionSchema
  // -----------------------------------------------------------------------
  describe('kbPermissionSchema', () => {
    it('should accept valid user permission', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: ['user1'], role: 'READER' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept valid team permission', () => {
      const result = kbPermissionSchema.safeParse({
        body: { teamIds: ['team1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject when no userIds or teamIds', () => {
      const result = kbPermissionSchema.safeParse({
        body: { role: 'READER' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject when userIds provided without role', () => {
      const result = kbPermissionSchema.safeParse({
        body: { userIds: ['user1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // updatePermissionsSchema
  // -----------------------------------------------------------------------
  describe('updatePermissionsSchema', () => {
    it('should reject teamIds in update', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'WRITER', teamIds: ['team1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });

    it('should accept userIds with role', () => {
      const result = updatePermissionsSchema.safeParse({
        body: { role: 'WRITER', userIds: ['user1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // listKnowledgeBasesSchema
  // -----------------------------------------------------------------------
  describe('listKnowledgeBasesSchema', () => {
    it('should accept valid query params', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { page: '1', limit: '20', sortBy: 'name', sortOrder: 'asc' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject unknown query params (strict mode)', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { unknownParam: 'value' },
      });
      expect(result.success).to.be.false;
    });

    it('should accept search with permissions filter', () => {
      const result = listKnowledgeBasesSchema.safeParse({
        query: { permissions: 'OWNER,READER' },
      });
      expect(result.success).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // moveRecordSchema
  // -----------------------------------------------------------------------
  describe('moveRecordSchema', () => {
    it('should accept valid move request', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: 'folder-123' },
        params: {
          kbId: '550e8400-e29b-41d4-a716-446655440000',
          recordId: 'rec-1',
        },
      });
      expect(result.success).to.be.true;
    });

    it('should accept null newParentId (move to root)', () => {
      const result = moveRecordSchema.safeParse({
        body: { newParentId: null },
        params: {
          kbId: '550e8400-e29b-41d4-a716-446655440000',
          recordId: 'rec-1',
        },
      });
      expect(result.success).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // Simple schemas
  // -----------------------------------------------------------------------
  describe('simple schemas', () => {
    it('getRecordByIdSchema should accept valid input', () => {
      const result = getRecordByIdSchema.safeParse({
        params: { recordId: 'rec-1' },
        query: { convertTo: 'pdf' },
      });
      expect(result.success).to.be.true;
    });

    it('reindexRecordSchema should accept depth', () => {
      const result = reindexRecordSchema.safeParse({
        params: { recordId: 'rec-1' },
        body: { depth: 5 },
      });
      expect(result.success).to.be.true;
    });

    it('reindexRecordGroupSchema should accept depth', () => {
      const result = reindexRecordGroupSchema.safeParse({
        params: { recordGroupId: 'grp-1' },
        body: { depth: -1 },
      });
      expect(result.success).to.be.true;
    });

    it('createKBSchema should accept valid input', () => {
      const result = createKBSchema.safeParse({
        body: { kbName: 'My KB' },
      });
      expect(result.success).to.be.true;
    });

    it('updateKBSchema should accept valid input', () => {
      const result = updateKBSchema.safeParse({
        body: { kbName: 'Updated KB' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('createFolderSchema should accept valid input', () => {
      const result = createFolderSchema.safeParse({
        body: { folderName: 'New Folder' },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
        query: { folderId: 'parent-folder-1' },
      });
      expect(result.success).to.be.true;
    });

    it('deletePermissionsSchema should accept valid input', () => {
      const result = deletePermissionsSchema.safeParse({
        body: { userIds: ['u1'], teamIds: ['t1'] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });
  });

  // -----------------------------------------------------------------------
  // rejectedFiles schema validation (new in this PR)
  // -----------------------------------------------------------------------
  describe('rejectedFiles schema in uploadRecordsSchema', () => {
    it('should accept valid rejectedFiles with proper FileRejectionReason', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          rejectedFiles: [
            {
              originalname: 'bad.exe',
              filePath: 'KB/bad.exe',
              size: 1024,
              mimetype: 'application/octet-stream',
              reason: FileRejectionReason.UNSUPPORTED_TYPE,
              error: 'Unsupported file type ".exe"',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept rejectedFiles with EXCEEDS_SIZE_LIMIT reason', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          rejectedFiles: [
            {
              originalname: 'huge.pdf',
              filePath: 'KB/huge.pdf',
              size: 200_000_000,
              mimetype: 'application/pdf',
              reason: FileRejectionReason.EXCEEDS_SIZE_LIMIT,
              error: 'File exceeds the 100 MB size limit',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept rejectedFiles with DUPLICATE_NAME reason', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          rejectedFiles: [
            {
              originalname: 'dup.pdf',
              filePath: 'KB/dup.pdf',
              size: 500,
              mimetype: 'application/pdf',
              reason: FileRejectionReason.DUPLICATE_NAME,
              error: 'A file with this name already exists',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject rejectedFiles with an invalid reason string', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          rejectedFiles: [
            {
              originalname: 'bad.pdf',
              filePath: 'KB/bad.pdf',
              size: 500,
              mimetype: 'application/pdf',
              reason: 'INVALID_REASON',
              error: 'some error',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });

    it('should reject rejectedFiles missing required fields', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          rejectedFiles: [
            {
              originalname: 'bad.pdf',
              // missing filePath, size, mimetype, reason, error
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });

    it('should accept empty rejectedFiles array', () => {
      const result = uploadRecordsSchema.safeParse({
        body: { rejectedFiles: [] },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should accept omitted rejectedFiles (optional)', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {},
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });
  });

  describe('rejectedFiles schema in uploadRecordsSchema (folder query)', () => {
    it('should accept valid rejectedFiles with folderId query', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          rejectedFiles: [
            {
              originalname: 'bad.exe',
              filePath: 'KB/folder/bad.exe',
              size: 1024,
              mimetype: 'application/octet-stream',
              reason: FileRejectionReason.UNSUPPORTED_TYPE,
              error: 'Unsupported file type ".exe"',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
        query: { folderId: 'folder-1' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject invalid reason in rejectedFiles with folderId query', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          rejectedFiles: [
            {
              originalname: 'bad.pdf',
              filePath: 'KB/bad.pdf',
              size: 500,
              mimetype: 'application/pdf',
              reason: 'NOT_A_VALID_REASON',
              error: 'error',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
        query: { folderId: 'folder-1' },
      });
      expect(result.success).to.be.false;
    });
  });

  // -----------------------------------------------------------------------
  // fileBufferSchema MIME type validation
  // -----------------------------------------------------------------------
  describe('fileBufferSchema MIME type validation in uploadRecordsSchema', () => {
    it('should accept a fileBuffer with a valid MIME type from the registry', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          fileBuffers: [
            {
              buffer: Buffer.from('test'),
              mimetype: 'application/pdf',
              originalname: 'test.pdf',
              size: 4,
              lastModified: Date.now(),
              filePath: '/path/to/test.pdf',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.true;
    });

    it('should reject a fileBuffer with an unknown MIME type', () => {
      const result = uploadRecordsSchema.safeParse({
        body: {
          fileBuffers: [
            {
              buffer: Buffer.from('test'),
              mimetype: 'application/x-totally-unknown',
              originalname: 'test.xyz',
              size: 4,
              lastModified: Date.now(),
              filePath: '/path/to/test.xyz',
            },
          ],
        },
        params: { kbId: '550e8400-e29b-41d4-a716-446655440000' },
      });
      expect(result.success).to.be.false;
    });
  });
});
