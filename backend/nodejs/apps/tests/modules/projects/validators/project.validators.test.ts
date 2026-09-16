import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import {
  createProjectSchema,
  updateProjectSchema,
  projectIdParamsSchema,
  listProjectsQuerySchema,
  listProjectConversationsQuerySchema,
  removeProjectFileParamsSchema,
  upsertProjectMembersSchema,
  removeProjectMemberParamsSchema,
} from '../../../../src/modules/projects/validators/project.validators'
import { PROJECT_NAME_MAX_LENGTH } from '../../../../src/modules/projects/constants/constants'

const VALID_OID = new mongoose.Types.ObjectId().toString()

describe('projects/validators/project.validators', () => {
  describe('createProjectSchema', () => {
    it('accepts a minimal valid body (name only)', () => {
      const result = createProjectSchema.safeParse({ body: { name: 'My Project' } })
      expect(result.success).to.equal(true)
    })

    it('trims the project name', () => {
      const result = createProjectSchema.safeParse({ body: { name: '  Trimmed  ' } })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.body.name).to.equal('Trimmed')
      }
    })

    it('rejects an empty name', () => {
      const result = createProjectSchema.safeParse({ body: { name: '' } })
      expect(result.success).to.equal(false)
    })

    it('rejects a whitespace-only name (trimmed to empty)', () => {
      const result = createProjectSchema.safeParse({ body: { name: '   ' } })
      expect(result.success).to.equal(false)
    })

    it('rejects a missing name', () => {
      const result = createProjectSchema.safeParse({ body: {} })
      expect(result.success).to.equal(false)
    })

    it('rejects a name over the max length', () => {
      const result = createProjectSchema.safeParse({
        body: { name: 'a'.repeat(PROJECT_NAME_MAX_LENGTH + 1) },
      })
      expect(result.success).to.equal(false)
    })

    it('accepts a name at exactly the max length', () => {
      const result = createProjectSchema.safeParse({
        body: { name: 'a'.repeat(PROJECT_NAME_MAX_LENGTH) },
      })
      expect(result.success).to.equal(true)
    })

    it('accepts optional description/icon/color/instructions/knowledgeScope/appliedFilters', () => {
      const result = createProjectSchema.safeParse({
        body: {
          name: 'Full',
          description: 'A description',
          icon: 'folder',
          color: '#fff',
          instructions: 'Be concise',
          knowledgeScope: { apps: ['app-1'], kb: ['kb-1'] },
          appliedFilters: {
            apps: [{ id: 'a1', name: 'App 1', nodeType: 'app', connector: 'gdrive' }],
          },
        },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects a malformed knowledgeScope entry (non-string id)', () => {
      const result = createProjectSchema.safeParse({
        body: { name: 'X', knowledgeScope: { apps: [123] } },
      })
      expect(result.success).to.equal(false)
    })

    it('strips visibility/chatSharing from the create body (owner-only fields set on update)', () => {
      const result = createProjectSchema.safeParse({
        body: { name: 'X', visibility: 'org' },
      })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect((result.data.body as any).visibility).to.be.undefined
      }
    })
  })

  describe('updateProjectSchema', () => {
    it('accepts a partial patch with a valid projectId param', () => {
      const result = updateProjectSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { name: 'Renamed' },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects an invalid projectId param', () => {
      const result = updateProjectSchema.safeParse({
        params: { projectId: 'not-an-object-id' },
        body: { name: 'Renamed' },
      })
      expect(result.success).to.equal(false)
    })

    it('accepts an empty body (no-op patch)', () => {
      const result = updateProjectSchema.safeParse({
        params: { projectId: VALID_OID },
        body: {},
      })
      expect(result.success).to.equal(true)
    })

    it('accepts a valid visibility value', () => {
      const result = updateProjectSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { visibility: 'org' },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects an invalid visibility value', () => {
      const result = updateProjectSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { visibility: 'public' },
      })
      expect(result.success).to.equal(false)
    })

    it('accepts a valid chatSharing value', () => {
      const result = updateProjectSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { chatSharing: 'members' },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects an empty name in a patch (name explicitly provided but blank)', () => {
      const result = updateProjectSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { name: '' },
      })
      expect(result.success).to.equal(false)
    })
  })

  describe('projectIdParamsSchema', () => {
    it('accepts a valid ObjectId', () => {
      const result = projectIdParamsSchema.safeParse({ params: { projectId: VALID_OID } })
      expect(result.success).to.equal(true)
    })

    it('rejects a malformed ObjectId', () => {
      const result = projectIdParamsSchema.safeParse({ params: { projectId: '123' } })
      expect(result.success).to.equal(false)
    })
  })

  describe('listProjectsQuerySchema', () => {
    it('defaults page/limit/scope when omitted', () => {
      const result = listProjectsQuerySchema.safeParse({ query: {} })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.query.page).to.equal(1)
        expect(result.data.query.limit).to.equal(20)
        expect(result.data.query.scope).to.equal('mine')
      }
    })

    it('accepts scope=shared and scope=all', () => {
      expect(
        listProjectsQuerySchema.safeParse({ query: { scope: 'shared' } }).success,
      ).to.equal(true)
      expect(
        listProjectsQuerySchema.safeParse({ query: { scope: 'all' } }).success,
      ).to.equal(true)
    })

    it('rejects an invalid scope value', () => {
      const result = listProjectsQuerySchema.safeParse({ query: { scope: 'everyone' } })
      expect(result.success).to.equal(false)
    })

    it('rejects a limit over 100', () => {
      const result = listProjectsQuerySchema.safeParse({ query: { limit: '101' } })
      expect(result.success).to.equal(false)
    })

    it('transforms includeArchived "true"/"false" strings to booleans', () => {
      const trueResult = listProjectsQuerySchema.safeParse({
        query: { includeArchived: 'true' },
      })
      const falseResult = listProjectsQuerySchema.safeParse({
        query: { includeArchived: 'false' },
      })
      expect(trueResult.success && trueResult.data.query.includeArchived).to.equal(true)
      expect(falseResult.success && falseResult.data.query.includeArchived).to.equal(false)
    })

    it('rejects a non-boolean-string includeArchived', () => {
      const result = listProjectsQuerySchema.safeParse({ query: { includeArchived: 'yes' } })
      expect(result.success).to.equal(false)
    })
  })

  describe('listProjectConversationsQuerySchema', () => {
    it('accepts a valid projectId with default pagination', () => {
      const result = listProjectConversationsQuerySchema.safeParse({
        params: { projectId: VALID_OID },
        query: {},
      })
      expect(result.success).to.equal(true)
    })

    it('rejects a malformed projectId', () => {
      const result = listProjectConversationsQuerySchema.safeParse({
        params: { projectId: 'bad' },
        query: {},
      })
      expect(result.success).to.equal(false)
    })
  })

  describe('removeProjectFileParamsSchema', () => {
    it('accepts a valid projectId + recordId', () => {
      const result = removeProjectFileParamsSchema.safeParse({
        params: { projectId: VALID_OID, recordId: 'record-1' },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects an empty recordId', () => {
      const result = removeProjectFileParamsSchema.safeParse({
        params: { projectId: VALID_OID, recordId: '' },
      })
      expect(result.success).to.equal(false)
    })
  })

  describe('upsertProjectMembersSchema', () => {
    it('accepts a valid members array', () => {
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: [{ principalId: VALID_OID, role: 'viewer' }] },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects an empty members array', () => {
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: [] },
      })
      expect(result.success).to.equal(false)
    })

    it('rejects an invalid member role', () => {
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: [{ principalId: VALID_OID, role: 'admin' }] },
      })
      expect(result.success).to.equal(false)
    })

    it('rejects a malformed principalId', () => {
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: [{ principalId: 'not-an-id', role: 'viewer' }] },
      })
      expect(result.success).to.equal(false)
    })

    it('rejects a members array exceeding the batch limit', () => {
      const overLimit = Array.from({ length: 51 }, (_, i) => ({
        principalId: new mongoose.Types.ObjectId().toString(),
        role: 'viewer' as const,
      }))
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: overLimit },
      })
      expect(result.success).to.equal(false)
    })

    it('accepts members array at exactly the batch limit', () => {
      const atLimit = Array.from({ length: 50 }, () => ({
        principalId: new mongoose.Types.ObjectId().toString(),
        role: 'editor' as const,
      }))
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: atLimit },
      })
      expect(result.success).to.equal(true)
    })
  })

  describe('removeProjectMemberParamsSchema', () => {
    it('accepts valid projectId + memberUserId', () => {
      const result = removeProjectMemberParamsSchema.safeParse({
        params: { projectId: VALID_OID, memberUserId: VALID_OID },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects a malformed memberUserId', () => {
      const result = removeProjectMemberParamsSchema.safeParse({
        params: { projectId: VALID_OID, memberUserId: 'bad' },
      })
      expect(result.success).to.equal(false)
    })
  })
})
