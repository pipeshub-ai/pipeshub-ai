import 'reflect-metadata'
import { expect } from 'chai'
import {
  listKnowledgeBasesResponseSchema,
  kbSuccessResponseSchema,
  createKBResponseSchema,
  getKBResponseSchema,
  getRecordByIdResponseSchema,
  updateRecordResponseSchema,
  deleteRecordResponseSchema,
  reindexRecordResponseSchema,
  reindexRecordGroupResponseSchema,
  getConnectorStatsResponseSchema,
  reindexFailedRecordsResponseSchema,
  resyncConnectorRecordsResponseSchema,
  getKbUploadLimitsResponseSchema,
  moveRecordResponseSchema,
  listKBPermissionsResponseSchema,
  removeKBPermissionResponseSchema,
  updateKBPermissionResponseSchema,
  createKBPermissionResponseSchema,
  uploadRecordsResponseSchema,
  createFolderResponseSchema,
  kbFolderSuccessResponseSchema,
  getKnowledgeHubNodesResponseSchema,
  getKBChildrenResponseSchema,
  getFolderChildrenResponseSchema,
} from '../../../../src/modules/knowledge_base/validators/validators'

const TS = 1775196920754

const validIndexingStatusCounts = {
  NOT_STARTED: 0,
  PAUSED: 0,
  IN_PROGRESS: 5,
  COMPLETED: 100,
  FAILED: 2,
  FILE_TYPE_NOT_SUPPORTED: 0,
  AUTO_INDEX_OFF: 0,
  EMPTY: 0,
  ENABLE_MULTIMODAL_MODELS: 0,
  QUEUED: 3,
}

describe('Response Schema Validation', () => {
  // =========================================================================
  // 1. listKnowledgeBasesResponseSchema
  // =========================================================================
  describe('listKnowledgeBasesResponseSchema', () => {
    const validData = {
      knowledgeBases: [
        {
          id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
          name: 'Engineering KB',
          connectorId: 'conn-abc-123',
          createdAtTimestamp: TS,
          updatedAtTimestamp: TS + 1000,
          createdBy: 'user-001',
          userRole: 'OWNER',
          folders: [
            { id: 'folder-001', name: 'Docs', createdAtTimestamp: TS, webUrl: 'https://example.com/docs' },
          ],
        },
      ],
      pagination: { page: 1, limit: 20, totalCount: 1, totalPages: 1, hasNext: false, hasPrev: false },
      filters: {
        applied: { search: 'test', permissions: ['OWNER'], sort_by: 'name', sort_order: 'asc' },
        available: {
          permissions: ['OWNER', 'ORGANIZER', 'FILEORGANIZER', 'WRITER', 'COMMENTER', 'READER'],
          sortFields: ['name', 'createdAtTimestamp', 'updatedAtTimestamp', 'userRole'],
          sortOrders: ['asc', 'desc'],
        },
      },
    }

    it('should accept valid complete response', () => {
      const result = listKnowledgeBasesResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should reject null connectorId (list item requires non-null string)', () => {
      const data = {
        ...validData,
        knowledgeBases: [
          {
            ...validData.knowledgeBases[0],
            connectorId: null,
          },
        ],
      }
      const result = listKnowledgeBasesResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject userRole as null (required string)', () => {
      const data = {
        ...validData,
        knowledgeBases: [{ ...validData.knowledgeBases[0], userRole: null }],
      }
      const result = listKnowledgeBasesResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject folders as null (required array)', () => {
      const data = {
        ...validData,
        knowledgeBases: [{ ...validData.knowledgeBases[0], folders: null }],
      }
      const result = listKnowledgeBasesResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject folder missing createdAtTimestamp or webUrl (list KB always returns both)', () => {
      const missingTs = listKnowledgeBasesResponseSchema.safeParse({
        ...validData,
        knowledgeBases: [
          {
            ...validData.knowledgeBases[0],
            folders: [{ id: 'f-1', name: 'F', webUrl: 'https://example.com/f' }],
          },
        ],
      })
      const missingUrl = listKnowledgeBasesResponseSchema.safeParse({
        ...validData,
        knowledgeBases: [
          {
            ...validData.knowledgeBases[0],
            folders: [{ id: 'f-1', name: 'F', createdAtTimestamp: TS }],
          },
        ],
      })
      expect(missingTs.success).to.be.false
      expect(missingUrl.success).to.be.false
    })

    it('should reject null createdAtTimestamp or webUrl on folder', () => {
      const data = {
        ...validData,
        knowledgeBases: [
          {
            ...validData.knowledgeBases[0],
            folders: [{ id: 'f-1', name: 'F', createdAtTimestamp: null, webUrl: null }],
          },
        ],
      }
      const result = listKnowledgeBasesResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept folder webUrl as relative path (connector list KB shape)', () => {
      const data = {
        ...validData,
        knowledgeBases: [
          {
            ...validData.knowledgeBases[0],
            connectorId: 'knowledgeBase_org-1',
            folders: [
              {
                id: '3c5f7c05-3c9d-45c6-bd0e-ba305cd9715d',
                name: 'djf',
                createdAtTimestamp: 1775504506285,
                webUrl: '/kb/e3c8032f-29e4-4cc0-910a-454ac0ad9531/folder/3c5f7c05-3c9d-45c6-bd0e-ba305cd9715d',
              },
            ],
          },
        ],
      }
      const result = listKnowledgeBasesResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { pagination, ...rest } = validData
      const result = listKnowledgeBasesResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = listKnowledgeBasesResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 2. kbSuccessResponseSchema
  // =========================================================================
  describe('kbSuccessResponseSchema', () => {
    it('should accept valid complete response', () => {
      const result = kbSuccessResponseSchema.safeParse({ success: true, message: 'Done' })
      expect(result.success).to.be.true
    })

    it('should accept without optional message', () => {
      const result = kbSuccessResponseSchema.safeParse({ success: false })
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const result = kbSuccessResponseSchema.safeParse({ message: 'Done' })
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = kbSuccessResponseSchema.safeParse({ success: true, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 3. createKBResponseSchema
  // =========================================================================
  describe('createKBResponseSchema', () => {
    const validData = {
      id: 'kb-9f8e7d6c-5b4a-3210-fedc-ba9876543210',
      name: 'Product Knowledge Base',
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS,
      userRole: 'OWNER',
    }

    it('should accept valid complete response', () => {
      const result = createKBResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { userRole, ...rest } = validData
      const result = createKBResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = createKBResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 4. getKBResponseSchema
  // =========================================================================
  describe('getKBResponseSchema', () => {
    const validData = {
      id: 'kb-001',
      name: 'Main KB',
      connectorId: null,
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS + 5000,
      createdBy: 'user-admin',
      userRole: 'OWNER',
      folders: [{ id: 'f-001', name: 'Root Folder', createdAtTimestamp: TS, webUrl: 'https://example.com/f' }],
    }

    it('should accept valid complete response', () => {
      const result = getKBResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with nullable connectorId as null', () => {
      const result = getKBResponseSchema.safeParse({ ...validData, connectorId: null })
      expect(result.success).to.be.true
    })

    it('should accept with empty folders array', () => {
      const result = getKBResponseSchema.safeParse({ ...validData, folders: [] })
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { createdBy, ...rest } = validData
      const result = getKBResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject empty id', () => {
      const result = getKBResponseSchema.safeParse({ ...validData, id: '' })
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = getKBResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 5. getRecordByIdResponseSchema
  // =========================================================================
  describe('getRecordByIdResponseSchema', () => {
    const baseRecord = {
      id: 'rec-a1b2c3d4',
      _id: 'records/rec-a1b2c3d4',
      recordName: 'Architecture Overview.pdf',
      recordType: 'FILE',
      origin: 'UPLOAD',
      orgId: 'org-12345',
      externalRecordId: 'ext-rec-001',
      connectorId: 'knowledgeBase_org-12345',
      connectorName: 'KB',
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS + 3000,
      version: 1,
      indexingStatus: 'COMPLETED',
      isDeleted: false,
      isArchived: false,
      fileRecord: null,
      mailRecord: null,
      ticketRecord: null,
    }

    const validFileRecord = {
      id: 'fr-001',
      _id: 'fileRecords/fr-001',
      name: 'Architecture Overview.pdf',
      isFile: true,
    }

    const validMailRecord = {
      id: 'mr-001',
      _id: 'mailRecords/mr-001',
      threadId: 'thread-xyz',
      isParent: true,
      labels: [],
      assignee_source_id: [],
      is_email_hidden: false,
    }

    const validTicketRecord = {
      id: 'tr-001',
      _id: 'ticketRecords/tr-001',
      labels: ['bug', 'critical'],
      assignee_source_id: ['src-001'],
      is_email_hidden: false,
    }

    const validMetadata = {
      departments: [{ id: 'dep-1', name: 'Engineering' }],
      categories: [{ id: 'cat-1', name: 'Technical' }],
      subcategories1: [],
      subcategories2: [],
      subcategories3: [],
      topics: [{ id: 'top-1', name: 'Architecture' }],
      languages: [{ id: 'lang-1', name: 'English' }],
    }

    const validPermission = {
      id: 'perm-001',
      name: 'Admin User',
      type: 'USER',
      relationship: 'DIRECT',
      accessType: 'WRITE',
    }

    const validData = {
      record: baseRecord,
      knowledgeBase: { id: 'kb-001', name: 'Main KB', orgId: 'org-12345' },
      folder: { id: 'fold-001', name: 'Docs' },
      metadata: validMetadata,
      permissions: [validPermission],
    }

    it('should accept valid complete response', () => {
      const data = {
        ...validData,
        record: {
          ...baseRecord,
          _key: 'rec-a1b2c3d4',
          _rev: '_rev1',
          connectorName: 'Google Drive',
          isDirty: false,
          isLatestVersion: true,
          previewRenderable: true,
          hideWeburl: false,
          isDependentNode: false,
          isInternal: false,
          isShared: false,
          webUrl: 'https://example.com/rec',
          mimeType: 'application/pdf',
          fileRecord: {
            ...validFileRecord,
            _key: 'fr-001',
            _rev: '_rev2',
            orgId: 'org-12345',
            extension: '.pdf',
            etag: 'etag-1',
            ctag: 'ctag-1',
            md5Checksum: 'abc123',
            quickXorHash: null,
            crc32Hash: null,
            sha1Hash: null,
            sha256Hash: null,
            path: '/docs/Architecture Overview.pdf',
            mimeType: 'application/pdf',
            webUrl: 'https://example.com/file',
            sizeInBytes: 1048576,
          },
        },
      }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept with nullable fields as null', () => {
      const data = {
        ...validData,
        knowledgeBase: null,
        folder: null,
        metadata: null,
      }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept FILE record with fileRecord and null mail/ticket', () => {
      const data = {
        ...validData,
        record: {
          ...baseRecord,
          fileRecord: validFileRecord,
          mailRecord: null,
          ticketRecord: null,
        },
      }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept TICKET record with ticketRecord and null file/mail', () => {
      const data = {
        ...validData,
        record: {
          ...baseRecord,
          recordType: 'TICKET',
          fileRecord: null,
          mailRecord: null,
          ticketRecord: validTicketRecord,
        },
      }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept record with minimal optional fields', () => {
      const data = {
        record: baseRecord,
        knowledgeBase: null,
        folder: null,
        metadata: null,
        permissions: [],
      }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject knowledgeBase with null name', () => {
      const data = {
        ...validData,
        knowledgeBase: {
          id: 'kb-001',
          name: null,
          orgId: 'org-12345',
        } as unknown as (typeof validData)['knowledgeBase'],
      }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject record with null connectorId', () => {
      const data = {
        ...validData,
        record: { ...baseRecord, connectorId: null as unknown as string },
      }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject record with missing connectorName', () => {
      const { connectorName: _cn, ...recordNoName } = baseRecord
      const data = { ...validData, record: recordNoName }
      const result = getRecordByIdResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject record with null or empty connectorName', () => {
      for (const connectorName of [null, ''] as const) {
        const data = {
          ...validData,
          record: { ...baseRecord, connectorName: connectorName as unknown as string },
        }
        const result = getRecordByIdResponseSchema.safeParse(data)
        expect(result.success).to.be.false
      }
    })

    it('should reject missing required field', () => {
      const { record, ...rest } = validData
      const result = getRecordByIdResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = getRecordByIdResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 6. updateRecordResponseSchema
  // =========================================================================
  describe('updateRecordResponseSchema', () => {
    const validRecord = {
      _key: 'rec-key-001',
      _id: 'records/rec-key-001',
      _rev: '_rev1',
      recordName: 'Updated Document.docx',
      recordType: 'FILE',
      origin: 'UPLOAD',
      orgId: 'org-12345',
      externalRecordId: 'ext-001',
      connectorId: 'knowledgeBase_org-12345',
      connectorName: 'KB',
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS + 5000,
      version: 2,
      indexingStatus: 'COMPLETED',
      extractionStatus: 'COMPLETED',
      isDeleted: false,
      isArchived: false,
      isVLMOcrProcessed: false,
      webUrl: '/record/rec-key-001',
      mimeType: 'application/pdf',
      externalGroupId: 'kb-1',
      externalParentId: null,
      externalRootGroupId: 'kb-1',
      sourceCreatedAtTimestamp: TS,
      sourceLastModifiedTimestamp: TS,
      sizeInBytes: 12345,
    }

    const validData = {
      message: 'Record updated successfully',
      record: validRecord,
      fileUploaded: true,
      meta: { requestId: 'req-abc-123', timestamp: '2026-04-05T10:00:00Z' },
    }

    it('should accept valid complete response', () => {
      const result = updateRecordResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with nullable optional fields as null (connectorId stays non-empty)', () => {
      const data = {
        ...validData,
        record: {
          ...validRecord,
          webUrl: null,
          mimeType: null,
          externalGroupId: null,
          externalParentId: null,
          externalRootGroupId: null,
          sourceCreatedAtTimestamp: null,
          sourceLastModifiedTimestamp: null,
          sizeInBytes: null,
        },
      }
      const result = updateRecordResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject update record response when connectorId is null', () => {
      const data = {
        ...validData,
        record: { ...validRecord, connectorId: null as unknown as string },
      }
      const result = updateRecordResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject update record response when connectorName is missing', () => {
      const { connectorName: _cn, ...recordNoName } = validRecord
      const data = { ...validData, record: recordNoName }
      const result = updateRecordResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject update record response when connectorName is null or empty', () => {
      for (const connectorName of [null, ''] as const) {
        const data = {
          ...validData,
          record: { ...validRecord, connectorName: connectorName as unknown as string },
        }
        const result = updateRecordResponseSchema.safeParse(data)
        expect(result.success).to.be.false
      }
    })

    it('should accept older doc without fields added later', () => {
      const result = updateRecordResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept newer doc with all optional fields present', () => {
      const data = {
        ...validData,
        record: {
          ...validRecord,
          isDirty: false,
          isLatestVersion: true,
          previewRenderable: true,
          hideWeburl: false,
          isDependentNode: false,
          isInternal: false,
          isShared: false,
          externalRevisionId: null,
          virtualRecordId: 'vr-1',
          md5Checksum: 'abc123',
          parentNodeId: null,
          lastSyncTimestamp: TS,
          lastExtractionTimestamp: TS,
          lastIndexTimestamp: TS,
        },
      }
      const result = updateRecordResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { fileUploaded, ...rest } = validData
      const result = updateRecordResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = updateRecordResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 7. deleteRecordResponseSchema
  // =========================================================================
  describe('deleteRecordResponseSchema', () => {
    const validData = {
      success: true,
      message: 'Record deleted successfully',
      recordId: 'rec-del-001',
      connector: 'Google Drive',
      timestamp: TS,
    }

    it('should accept valid complete response', () => {
      const result = deleteRecordResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with nullable fields as null', () => {
      const result = deleteRecordResponseSchema.safeParse({
        ...validData,
        connector: null,
        timestamp: null,
      })
      expect(result.success).to.be.true
    })

    it('should accept timestamp as string', () => {
      const result = deleteRecordResponseSchema.safeParse({
        ...validData,
        timestamp: '2026-04-05T10:00:00Z',
      })
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { recordId, ...rest } = validData
      const result = deleteRecordResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject empty message', () => {
      const result = deleteRecordResponseSchema.safeParse({ ...validData, message: '' })
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = deleteRecordResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 8. reindexRecordResponseSchema
  // =========================================================================
  describe('reindexRecordResponseSchema', () => {
    const validData = {
      success: true,
      message: 'Record reindex initiated',
      recordId: 'rec-ridx-001',
      recordName: 'Quarterly Report.xlsx',
      connector: 'SharePoint',
      eventPublished: true,
      userRole: 'OWNER',
      depth: 0,
    }

    it('should accept valid complete response', () => {
      const result = reindexRecordResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept depth -1', () => {
      const result = reindexRecordResponseSchema.safeParse({ ...validData, depth: -1 })
      expect(result.success).to.be.true
    })

    it('should accept depth 100', () => {
      const result = reindexRecordResponseSchema.safeParse({ ...validData, depth: 100 })
      expect(result.success).to.be.true
    })

    it('should reject depth 101', () => {
      const result = reindexRecordResponseSchema.safeParse({ ...validData, depth: 101 })
      expect(result.success).to.be.false
    })

    it('should reject depth -2', () => {
      const result = reindexRecordResponseSchema.safeParse({ ...validData, depth: -2 })
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const { connector, ...rest } = validData
      const result = reindexRecordResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = reindexRecordResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 9. reindexRecordGroupResponseSchema
  // =========================================================================
  describe('reindexRecordGroupResponseSchema', () => {
    const validData = {
      success: true,
      message: 'Record group reindex started',
      recordGroupId: 'rg-001-abc',
      depth: 5,
      connector: 'Confluence',
      eventPublished: true,
    }

    it('should accept valid complete response', () => {
      const result = reindexRecordGroupResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { recordGroupId, ...rest } = validData
      const result = reindexRecordGroupResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = reindexRecordGroupResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 10. getConnectorStatsResponseSchema
  // =========================================================================
  describe('getConnectorStatsResponseSchema', () => {
    const validData = {
      success: true,
      data: {
        orgId: 'org-12345',
        connectorId: 'conn-gd-001',
        origin: 'CONNECTOR' as const,
        stats: {
          total: 110,
          indexingStatus: validIndexingStatusCounts,
        },
        byRecordType: [
          {
            recordType: 'FILE',
            total: 80,
            indexingStatus: validIndexingStatusCounts,
          },
          {
            recordType: 'MAIL',
            total: 30,
            indexingStatus: validIndexingStatusCounts,
          },
        ],
      },
    }

    it('should accept valid complete response', () => {
      const result = getConnectorStatsResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with empty byRecordType', () => {
      const data = { ...validData, data: { ...validData.data, byRecordType: [] } }
      const result = getConnectorStatsResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept non-CONNECTOR origin strings', () => {
      const data = { ...validData, data: { ...validData.data, origin: 'UPLOAD' } }
      const result = getConnectorStatsResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty origin', () => {
      const data = { ...validData, data: { ...validData.data, origin: '' } }
      const result = getConnectorStatsResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject negative stats count', () => {
      const data = {
        ...validData,
        data: {
          ...validData.data,
          stats: { total: -1, indexingStatus: validIndexingStatusCounts },
        },
      }
      const result = getConnectorStatsResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const { data, ...rest } = validData
      const result = getConnectorStatsResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = getConnectorStatsResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 11. reindexFailedRecordsResponseSchema
  // =========================================================================
  describe('reindexFailedRecordsResponseSchema', () => {
    it('should accept valid success response', () => {
      const result = reindexFailedRecordsResponseSchema.safeParse({
        reindexResponse: { success: true },
      })
      expect(result.success).to.be.true
    })

    it('should accept valid failure response with error', () => {
      const result = reindexFailedRecordsResponseSchema.safeParse({
        reindexResponse: { success: false, error: 'Connector not found' },
      })
      expect(result.success).to.be.true
    })

    it('should reject failure response without error string', () => {
      const result = reindexFailedRecordsResponseSchema.safeParse({
        reindexResponse: { success: false },
      })
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const result = reindexFailedRecordsResponseSchema.safeParse({})
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = reindexFailedRecordsResponseSchema.safeParse({
        reindexResponse: { success: true },
        extra: 'x',
      })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 12. resyncConnectorRecordsResponseSchema
  // =========================================================================
  describe('resyncConnectorRecordsResponseSchema', () => {
    it('should accept valid success response', () => {
      const result = resyncConnectorRecordsResponseSchema.safeParse({
        resyncConnectorResponse: { success: true },
      })
      expect(result.success).to.be.true
    })

    it('should accept valid failure response with error', () => {
      const result = resyncConnectorRecordsResponseSchema.safeParse({
        resyncConnectorResponse: { success: false, error: 'Sync failed' },
      })
      expect(result.success).to.be.true
    })

    it('should reject failure response without error string', () => {
      const result = resyncConnectorRecordsResponseSchema.safeParse({
        resyncConnectorResponse: { success: false },
      })
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const result = resyncConnectorRecordsResponseSchema.safeParse({})
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = resyncConnectorRecordsResponseSchema.safeParse({
        resyncConnectorResponse: { success: true },
        extra: 'x',
      })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 13. getKbUploadLimitsResponseSchema
  // =========================================================================
  describe('getKbUploadLimitsResponseSchema', () => {
    const validData = { maxFilesPerRequest: 10, maxFileSizeBytes: 104857600 }

    it('should accept valid complete response', () => {
      const result = getKbUploadLimitsResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should reject zero maxFilesPerRequest', () => {
      const result = getKbUploadLimitsResponseSchema.safeParse({ ...validData, maxFilesPerRequest: 0 })
      expect(result.success).to.be.false
    })

    it('should reject negative maxFileSizeBytes', () => {
      const result = getKbUploadLimitsResponseSchema.safeParse({ ...validData, maxFileSizeBytes: -1 })
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const result = getKbUploadLimitsResponseSchema.safeParse({ maxFilesPerRequest: 10 })
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = getKbUploadLimitsResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 14. moveRecordResponseSchema
  // =========================================================================
  describe('moveRecordResponseSchema', () => {
    it('should accept valid complete response', () => {
      const result = moveRecordResponseSchema.safeParse({ success: true, message: 'Record moved' })
      expect(result.success).to.be.true
    })

    it('should accept without optional message', () => {
      const result = moveRecordResponseSchema.safeParse({ success: true })
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const result = moveRecordResponseSchema.safeParse({ message: 'Moved' })
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = moveRecordResponseSchema.safeParse({ success: true, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 15. listKBPermissionsResponseSchema
  // =========================================================================
  describe('listKBPermissionsResponseSchema', () => {
    const validData = {
      kbId: 'kb-perm-001',
      permissions: [
        {
          id: 'perm-001',
          userId: 'user-001',
          email: 'admin@example.com',
          name: 'Admin User',
          role: 'OWNER',
          type: 'USER',
          createdAtTimestamp: TS,
          updatedAtTimestamp: TS + 1000,
        },
      ],
      totalCount: 1,
    }

    it('should accept valid complete response', () => {
      const result = listKBPermissionsResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with nullable fields as null', () => {
      const data = {
        ...validData,
        permissions: [
          {
            ...validData.permissions[0],
            userId: null,
            email: null,
            role: null,
          },
        ],
      }
      const result = listKBPermissionsResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { totalCount, ...rest } = validData
      const result = listKBPermissionsResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject empty kbId', () => {
      const result = listKBPermissionsResponseSchema.safeParse({ ...validData, kbId: '' })
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = listKBPermissionsResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 16. removeKBPermissionResponseSchema
  // =========================================================================
  describe('removeKBPermissionResponseSchema', () => {
    const validData = {
      kbId: 'kb-rem-001',
      userIds: ['user-001', 'user-002'],
      teamIds: ['team-001'],
    }

    it('should accept valid complete response', () => {
      const result = removeKBPermissionResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with empty arrays', () => {
      const result = removeKBPermissionResponseSchema.safeParse({
        kbId: 'kb-rem-001',
        userIds: [],
        teamIds: [],
      })
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { teamIds, ...rest } = validData
      const result = removeKBPermissionResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = removeKBPermissionResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 17. updateKBPermissionResponseSchema
  // =========================================================================
  describe('updateKBPermissionResponseSchema', () => {
    const validData = {
      kbId: 'kb-upd-001',
      userIds: ['user-003'],
      teamIds: [],
      newRole: 'WRITER',
    }

    it('should accept valid complete response', () => {
      const result = updateKBPermissionResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { newRole, ...rest } = validData
      const result = updateKBPermissionResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = updateKBPermissionResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 18. createKBPermissionResponseSchema
  // =========================================================================
  describe('createKBPermissionResponseSchema', () => {
    const validData = {
      kbId: 'kb-create-perm-001',
      permissionResult: {
        success: true,
        grantedCount: 2,
        grantedUsers: ['user-010', 'user-011'],
        grantedTeams: ['team-005'],
        role: 'READER',
        kbId: 'kb-create-perm-001',
        details: { note: 'Granted via API' },
      },
    }

    it('should accept valid complete response', () => {
      const result = createKBPermissionResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with empty details record', () => {
      const data = {
        ...validData,
        permissionResult: { ...validData.permissionResult, details: {} },
      }
      const result = createKBPermissionResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const { permissionResult, ...rest } = validData
      const result = createKBPermissionResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = createKBPermissionResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 19. uploadRecordsResponseSchema
  // =========================================================================
  describe('uploadRecordsResponseSchema', () => {
    const validData = {
      message: 'Upload processing started',
      totalFiles: 2,
      successfulFiles: 2,
      failedFiles: 0,
      status: 'processing' as const,
      failedFilesDetails: [],
      records: [
        {
          _key: 'rec-upl-001',
          recordName: 'Report.pdf',
          externalRecordId: 'ext-upl-001',
          recordType: 'FILE',
          origin: 'UPLOAD',
          indexingStatus: 'QUEUED',
          createdAtTimestamp: TS,
          updatedAtTimestamp: TS,
          sourceCreatedAtTimestamp: TS - 10000,
          sourceLastModifiedTimestamp: TS - 5000,
          version: 1,
          webUrl: 'https://example.com/files/report.pdf',
          mimeType: 'application/pdf',
          fileRecord: {
            _key: 'fr-upl-001',
            name: 'Report.pdf',
            extension: '.pdf',
            mimeType: 'application/pdf',
            sizeInBytes: 524288,
            webUrl: 'https://example.com/files/report.pdf',
          },
        },
      ],
    }

    it('should accept valid complete response', () => {
      const result = uploadRecordsResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should accept with nullable extension as null', () => {
      const data = {
        ...validData,
        records: [
          {
            ...validData.records[0],
            fileRecord: { ...validData.records[0].fileRecord, extension: null },
          },
        ],
      }
      const result = uploadRecordsResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept with failed files details', () => {
      const data = {
        ...validData,
        failedFiles: 1,
        failedFilesDetails: [
          { fileName: 'corrupt.bin', filePath: '/tmp/corrupt.bin', error: 'Invalid file format' },
        ],
      }
      const result = uploadRecordsResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept arbitrary non-empty status string', () => {
      const result = uploadRecordsResponseSchema.safeParse({ ...validData, status: 'completed' })
      expect(result.success).to.be.true
    })

    it('should reject empty status', () => {
      const result = uploadRecordsResponseSchema.safeParse({ ...validData, status: '' })
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const { records, ...rest } = validData
      const result = uploadRecordsResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = uploadRecordsResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 20. createFolderResponseSchema
  // =========================================================================
  describe('createFolderResponseSchema', () => {
    const validData = {
      id: 'folder-new-001',
      name: 'New Folder',
      webUrl: 'https://example.com/folders/new-folder',
    }

    it('should accept valid complete response', () => {
      const result = createFolderResponseSchema.safeParse(validData)
      expect(result.success).to.be.true
    })

    it('should reject empty id', () => {
      const result = createFolderResponseSchema.safeParse({ ...validData, id: '' })
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const { webUrl, ...rest } = validData
      const result = createFolderResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = createFolderResponseSchema.safeParse({ ...validData, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 21. kbFolderSuccessResponseSchema
  // =========================================================================
  describe('kbFolderSuccessResponseSchema', () => {
    it('should accept valid complete response', () => {
      const result = kbFolderSuccessResponseSchema.safeParse({ success: true, message: 'Folder updated successfully' })
      expect(result.success).to.be.true
    })

    it('should accept without optional message', () => {
      const result = kbFolderSuccessResponseSchema.safeParse({ success: true })
      expect(result.success).to.be.true
    })

    it('should reject missing required field', () => {
      const result = kbFolderSuccessResponseSchema.safeParse({ message: 'Done' })
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = kbFolderSuccessResponseSchema.safeParse({ success: true, extra: 'x' })
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 22. getKnowledgeHubNodesResponseSchema
  // =========================================================================
  describe('getKnowledgeHubNodesResponseSchema', () => {
    const validKHNodeItem = {
      id: 'node-1',
      name: 'My App',
      nodeType: 'app',
      parentId: null,
      origin: 'CONNECTOR',
      connector: 'GOOGLE_DRIVE',
      recordType: null,
      recordGroupType: null,
      indexingStatus: null,
      createdAt: TS,
      updatedAt: TS,
      sizeInBytes: null,
      mimeType: null,
      extension: null,
      webUrl: null,
      hasChildren: true,
      previewRenderable: null,
      permission: null,
      sharingStatus: null,
      isInternal: false,
    }

    const validKHPagination = {
      page: 1,
      limit: 50,
      totalItems: 5,
      totalPages: 1,
      hasNext: false,
      hasPrev: false,
    }

    const validKHFilters = {
      applied: {
        q: null,
        nodeTypes: null,
        recordTypes: null,
        origins: null,
        connectorIds: null,
        indexingStatus: null,
        createdAt: null,
        updatedAt: null,
        size: null,
        sortBy: 'name',
        sortOrder: 'asc',
      },
      available: null,
    }

    const validKHResponse = {
      success: true,
      error: null,
      id: null,
      currentNode: null,
      parentNode: null,
      items: [validKHNodeItem],
      pagination: validKHPagination,
      filters: validKHFilters,
      breadcrumbs: null,
      counts: null,
      permissions: null,
    }

    it('should accept valid root-level response', () => {
      const result = getKnowledgeHubNodesResponseSchema.safeParse(validKHResponse)
      expect(result.success).to.be.true
    })

    it('should accept COLLECTION node with connector KB (connectorName from graph)', () => {
      const kbRgItem = {
        ...validKHNodeItem,
        id: 'rg-kb-1',
        name: 'Team KB',
        nodeType: 'recordGroup',
        origin: 'COLLECTION',
        connector: 'KB',
        recordGroupType: 'KB',
        sharingStatus: 'private',
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse({
        ...validKHResponse,
        items: [kbRgItem],
      })
      expect(result.success).to.be.true
    })

    it('should reject node item with null connector', () => {
      const result = getKnowledgeHubNodesResponseSchema.safeParse({
        ...validKHResponse,
        items: [{ ...validKHNodeItem, connector: null }],
      })
      expect(result.success).to.be.false
    })

    it('should accept with currentNode and breadcrumbs for child browsing', () => {
      const data = {
        ...validKHResponse,
        id: 'app-123',
        currentNode: { id: 'app-123', name: 'Drive', nodeType: 'app', subType: 'GOOGLE_DRIVE' },
        parentNode: null,
        breadcrumbs: [{ id: 'app-123', name: 'Drive', nodeType: 'app', subType: 'GOOGLE_DRIVE' }],
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept with counts and permissions when included', () => {
      const data = {
        ...validKHResponse,
        counts: { items: [{ label: 'files', count: 10 }], total: 10 },
        permissions: {
          role: 'OWNER',
          canUpload: true,
          canCreateFolders: true,
          canEdit: true,
          canDelete: true,
          canManagePermissions: true,
        },
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept node item with permission', () => {
      const data = {
        ...validKHResponse,
        items: [{
          ...validKHNodeItem,
          permission: { role: 'WRITER', canEdit: true, canDelete: false },
        }],
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept available filters when included', () => {
      const filterOption = { id: 'opt-1', label: 'Files', type: null, connectorType: null }
      const data = {
        ...validKHResponse,
        filters: {
          ...validKHFilters,
          available: {
            nodeTypes: [filterOption],
            recordTypes: [filterOption],
            origins: [filterOption],
            connectors: [{ ...filterOption, connectorType: 'GOOGLE_DRIVE' }],
            indexingStatus: [filterOption],
            sortBy: [filterOption],
            sortOrder: [filterOption],
          },
        },
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept applied filters with date/size ranges', () => {
      const data = {
        ...validKHResponse,
        filters: {
          ...validKHFilters,
          applied: {
            ...validKHFilters.applied,
            q: 'search term',
            nodeTypes: ['app', 'record'],
            createdAt: { gte: TS, lte: TS + 1000 },
            size: { gte: 0, lte: 1000000 },
          },
        },
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept currentNode with subType (KB recordGroup uses COLLECTION from connector)', () => {
      const data = {
        ...validKHResponse,
        id: 'rg-1',
        currentNode: { id: 'rg-1', name: 'KB', nodeType: 'recordGroup', subType: 'COLLECTION' },
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject currentNode or breadcrumb missing subType', () => {
      const noSubCurrent = getKnowledgeHubNodesResponseSchema.safeParse({
        ...validKHResponse,
        id: 'app-123',
        currentNode: { id: 'app-123', name: 'Drive', nodeType: 'app' },
        breadcrumbs: [{ id: 'app-123', name: 'Drive', nodeType: 'app', subType: 'X' }],
      })
      const noSubCrumb = getKnowledgeHubNodesResponseSchema.safeParse({
        ...validKHResponse,
        id: 'app-123',
        currentNode: { id: 'app-123', name: 'Drive', nodeType: 'app', subType: 'GOOGLE_DRIVE' },
        breadcrumbs: [{ id: 'app-123', name: 'Drive', nodeType: 'app' }],
      })
      expect(noSubCurrent.success).to.be.false
      expect(noSubCrumb.success).to.be.false
    })

    it('should reject missing required field', () => {
      const { items, ...rest } = validKHResponse
      const result = getKnowledgeHubNodesResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = getKnowledgeHubNodesResponseSchema.safeParse({ ...validKHResponse, extra: 'x' })
      expect(result.success).to.be.false
    })

    it('should reject extra field in node item', () => {
      const data = {
        ...validKHResponse,
        items: [{ ...validKHNodeItem, extra: 'x' }],
      }
      const result = getKnowledgeHubNodesResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 23. getKBChildrenResponseSchema
  // =========================================================================
  describe('getKBChildrenResponseSchema', () => {
    const validContainer = {
      id: 'kb-1',
      name: 'My KB',
      path: '/',
      type: 'knowledgeBase',
      webUrl: '/kb/kb-1',
      recordGroupId: 'rg-1',
    }

    const validKBFolder = {
      id: 'folder-1',
      name: 'Documents',
      path: '/documents',
      level: 0,
      parent_id: null,
      webUrl: '/kb/kb-1/folder/folder-1',
      recordGroupId: 'rg-1',
      type: 'folder' as const,
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS,
      counts: { subfolders: 2, records: 5, totalItems: 7 },
      hasChildren: true,
    }

    const validKBRecord = {
      id: 'rec-1',
      recordName: 'report.pdf',
      name: 'report.pdf',
      recordType: 'FILE',
      externalRecordId: 'ext-1',
      origin: 'UPLOAD',
      connectorName: 'KB',
      indexingStatus: 'COMPLETED',
      version: 1,
      isLatestVersion: true,
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS,
      sourceCreatedAtTimestamp: TS,
      sourceLastModifiedTimestamp: TS,
      webUrl: '/record/rec-1',
      orgId: 'org-1',
      type: 'record' as const,
      fileRecord: {
        id: 'rec-1',
        name: 'report.pdf',
        extension: 'pdf',
        mimeType: 'application/pdf',
        sizeInBytes: 12345,
        webUrl: '/record/rec-1',
        path: '/documents/report.pdf',
        isFile: true,
      },
    }

    const validStringArrayFilters = {
      recordTypes: ['FILE'],
      origins: ['UPLOAD'],
      connectors: ['KB'],
      indexingStatus: ['COMPLETED'],
    }

    const validAppliedFilters = {
      sort_by: 'name',
      sort_order: 'asc',
    }

    const validUserPermission = {
      role: 'OWNER',
      canUpload: true,
      canCreateFolders: true,
      canEdit: true,
      canDelete: true,
      canManagePermissions: true,
    }

    const validChildrenPagination = {
      page: 1,
      limit: 50,
      totalItems: 12,
      totalPages: 1,
      hasNext: false,
      hasPrev: false,
    }

    const validKBChildrenCounts = {
      folders: 2,
      records: 10,
      totalItems: 12,
      totalFolders: 2,
      totalRecords: 10,
    }

    const validKBChildrenResponse = {
      success: true,
      container: validContainer,
      folders: [validKBFolder],
      records: [validKBRecord],
      level: 0,
      totalCount: 12,
      counts: validKBChildrenCounts,
      availableFilters: validStringArrayFilters,
      paginationMode: 'offset',
      userPermission: validUserPermission,
      pagination: validChildrenPagination,
      filters: {
        applied: validAppliedFilters,
        available: validStringArrayFilters,
      },
    }

    it('should accept valid complete response', () => {
      const result = getKBChildrenResponseSchema.safeParse(validKBChildrenResponse)
      expect(result.success).to.be.true
    })

    it('should accept with empty folders and records', () => {
      const data = {
        ...validKBChildrenResponse,
        folders: [],
        records: [],
        totalCount: 0,
        counts: { folders: 0, records: 0, totalItems: 0, totalFolders: 0, totalRecords: 0 },
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept record with null fileRecord', () => {
      const data = {
        ...validKBChildrenResponse,
        records: [{ ...validKBRecord, fileRecord: null }],
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept record with nullable timestamp fields as null', () => {
      const data = {
        ...validKBChildrenResponse,
        records: [{
          ...validKBRecord,
          sourceCreatedAtTimestamp: null,
          sourceLastModifiedTimestamp: null,
          webUrl: null,
        }],
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept applied filters with optional search and filters', () => {
      const data = {
        ...validKBChildrenResponse,
        filters: {
          applied: {
            ...validAppliedFilters,
            search: 'report',
            record_types: ['FILE'],
            origins: ['UPLOAD'],
            connectors: ['KB'],
            indexing_status: ['COMPLETED'],
          },
          available: validStringArrayFilters,
        },
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept folder item with arbitrary non-empty type string', () => {
      const data = {
        ...validKBChildrenResponse,
        folders: [{ ...validKBFolder, type: 'file' }],
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject folder item with empty type', () => {
      const data = {
        ...validKBChildrenResponse,
        folders: [{ ...validKBFolder, type: '' }],
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept record item with arbitrary non-empty type string', () => {
      const fileType = getKBChildrenResponseSchema.safeParse({
        ...validKBChildrenResponse,
        records: [{ ...validKBRecord, type: 'file' }],
      })
      const folderLabel = getKBChildrenResponseSchema.safeParse({
        ...validKBChildrenResponse,
        records: [{ ...validKBRecord, type: 'folder' }],
      })
      expect(fileType.success).to.be.true
      expect(folderLabel.success).to.be.true
    })

    it('should reject record item with empty type', () => {
      const data = {
        ...validKBChildrenResponse,
        records: [{ ...validKBRecord, type: '' }],
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject negative totalCount', () => {
      const data = { ...validKBChildrenResponse, totalCount: -1 }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const { container, ...rest } = validKBChildrenResponse
      const result = getKBChildrenResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = getKBChildrenResponseSchema.safeParse({ ...validKBChildrenResponse, extra: 'x' })
      expect(result.success).to.be.false
    })

    it('should reject extra field in container', () => {
      const data = {
        ...validKBChildrenResponse,
        container: { ...validContainer, extra: 'x' },
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject empty container id', () => {
      const data = {
        ...validKBChildrenResponse,
        container: { ...validContainer, id: '' },
      }
      const result = getKBChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // =========================================================================
  // 24. getFolderChildrenResponseSchema
  // =========================================================================
  describe('getFolderChildrenResponseSchema', () => {
    const validFolderChildFolder = {
      id: 'subfolder-1',
      name: 'Subfolder',
      path: '/documents/subfolder',
      level: 1,
      parent_id: 'folder-1',
      webUrl: '/kb/kb-1/folder/subfolder-1',
      recordGroupId: 'rg-1',
      type: 'folder' as const,
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS,
      counts: { subfolders: 0, records: 3, totalItems: 3 },
      hasChildren: true,
    }

    const validFolderChildRecord = {
      id: 'rec-2',
      recordName: 'notes.txt',
      name: 'notes.txt',
      recordType: 'FILE',
      externalRecordId: 'ext-2',
      origin: 'UPLOAD',
      connectorName: 'KB',
      indexingStatus: 'COMPLETED',
      version: 1,
      isLatestVersion: true,
      createdAtTimestamp: TS,
      updatedAtTimestamp: TS,
      sourceCreatedAtTimestamp: null,
      sourceLastModifiedTimestamp: null,
      webUrl: '/record/rec-2',
      orgId: 'org-1',
      type: 'record' as const,
      fileRecord: null,
    }

    const validStringArrayFilters = {
      recordTypes: ['FILE'],
      origins: ['UPLOAD'],
      connectors: ['KB'],
      indexingStatus: ['COMPLETED'],
    }

    const validFolderChildrenCounts = {
      folders: 1,
      records: 5,
      totalItems: 6,
      foldersShown: 1,
      recordsShown: 5,
    }

    const validUserPermission = {
      role: 'WRITER',
      canUpload: true,
      canCreateFolders: true,
      canEdit: true,
      canDelete: false,
      canManagePermissions: false,
    }

    const validPagination = {
      page: 1,
      limit: 50,
      totalItems: 6,
      totalPages: 1,
      hasNext: false,
      hasPrev: false,
    }

    const validFolderChildrenResponse = {
      success: true,
      folders: [validFolderChildFolder],
      records: [validFolderChildRecord],
      counts: validFolderChildrenCounts,
      totalCount: 6,
      availableFilters: validStringArrayFilters,
      userPermission: validUserPermission,
      pagination: validPagination,
      filters: {
        applied: { sort_by: 'name', sort_order: 'asc' },
        available: validStringArrayFilters,
      },
    }

    it('should accept valid complete response', () => {
      const result = getFolderChildrenResponseSchema.safeParse(validFolderChildrenResponse)
      expect(result.success).to.be.true
    })

    it('should accept with empty folders and records', () => {
      const data = {
        ...validFolderChildrenResponse,
        folders: [],
        records: [],
        totalCount: 0,
        counts: { folders: 0, records: 0, totalItems: 0, foldersShown: 0, recordsShown: 0 },
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept folder with nullable path as null', () => {
      const data = {
        ...validFolderChildrenResponse,
        folders: [{ ...validFolderChildFolder, path: null }],
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should require parent_id as string (not null)', () => {
      const data = {
        ...validFolderChildrenResponse,
        folders: [{ ...validFolderChildFolder, parent_id: null }],
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept applied filters with optional fields', () => {
      const data = {
        ...validFolderChildrenResponse,
        filters: {
          applied: {
            sort_by: 'updatedAtTimestamp',
            sort_order: 'desc',
            search: 'notes',
            record_types: ['FILE'],
          },
          available: validStringArrayFilters,
        },
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept folder item with arbitrary non-empty type string', () => {
      const data = {
        ...validFolderChildrenResponse,
        folders: [{ ...validFolderChildFolder, type: 'record' }],
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject folder item with empty type', () => {
      const data = {
        ...validFolderChildrenResponse,
        folders: [{ ...validFolderChildFolder, type: '' }],
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject negative counts', () => {
      const data = {
        ...validFolderChildrenResponse,
        counts: { ...validFolderChildrenCounts, folders: -1 },
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject missing required field', () => {
      const { counts, ...rest } = validFolderChildrenResponse
      const result = getFolderChildrenResponseSchema.safeParse(rest)
      expect(result.success).to.be.false
    })

    it('should reject extra unrecognized fields', () => {
      const result = getFolderChildrenResponseSchema.safeParse({ ...validFolderChildrenResponse, extra: 'x' })
      expect(result.success).to.be.false
    })

    it('should reject extra field in folder counts', () => {
      const data = {
        ...validFolderChildrenResponse,
        counts: { ...validFolderChildrenCounts, extra: 0 },
      }
      const result = getFolderChildrenResponseSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })
})
