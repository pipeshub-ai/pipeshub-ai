import 'reflect-metadata';
import { expect } from 'chai';
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
