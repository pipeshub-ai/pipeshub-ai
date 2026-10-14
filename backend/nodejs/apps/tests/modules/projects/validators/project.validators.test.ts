import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import {
  createProjectSchema,
  updateProjectSchema,
  projectIdParamsSchema,
  listProjectsQuerySchema,
  listProjectConversationsQuerySchema,
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

    it('accepts an explicit tools list of fullNames', () => {
      const result = createProjectSchema.safeParse({
        body: { name: 'X', tools: ['gmail.send_email', 'kb.search'] },
      })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.body.tools).to.deep.equal(['gmail.send_email', 'kb.search'])
      }
    })

    it('rejects a non-string entry in tools', () => {
      const result = createProjectSchema.safeParse({
        body: { name: 'X', tools: [123] },
      })
      expect(result.success).to.equal(false)
    })

    it('strips visibility/chatSharing from the create body (owner-only fields set on update)', () => {
      const result = createProjectSchema.safeParse({
        body: { name: 'X', visibility: 'org' },
      })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.body).to.not.have.property('visibility')
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

    it('coerces numeric query strings, accepting the limit boundary of 100', () => {
      const result = listProjectsQuerySchema.safeParse({ query: { page: '3', limit: '100' } })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.query.page).to.equal(3)
        expect(result.data.query.limit).to.equal(100)
      }
    })

    it('treats an empty page or limit (`?page=&limit=`) as omitted rather than as 0', () => {
      const result = listProjectsQuerySchema.safeParse({ query: { page: '', limit: '' } })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.query.page).to.equal(1)
        expect(result.data.query.limit).to.equal(20)
      }
    })

    for (const [label, query] of [
      ['page 0', { page: '0' }],
      ['a negative page', { page: '-1' }],
      ['a non-numeric page', { page: 'abc' }],
      ['limit 0', { limit: '0' }],
      ['a non-numeric limit', { limit: 'ten' }],
    ] as const) {
      it(`rejects ${label}`, () => {
        expect(listProjectsQuerySchema.safeParse({ query }).success).to.equal(false)
      })
    }

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

    it('transforms isArchived "true"/"false" strings while preserving an omitted value', () => {
      const trueResult = listProjectsQuerySchema.safeParse({
        query: { isArchived: 'true' },
      })
      const falseResult = listProjectsQuerySchema.safeParse({
        query: { isArchived: 'false' },
      })
      const omittedResult = listProjectsQuerySchema.safeParse({ query: {} })

      expect(trueResult.success && trueResult.data.query.isArchived).to.equal(true)
      expect(falseResult.success && falseResult.data.query.isArchived).to.equal(false)
      expect(
        omittedResult.success && omittedResult.data.query.isArchived,
      ).to.equal(undefined)
    })

    it('rejects a non-boolean-string isArchived', () => {
      const result = listProjectsQuerySchema.safeParse({ query: { isArchived: 'yes' } })
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

    it('accepts an explicit principalType of "team"', () => {
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: [{ principalId: VALID_OID, principalType: 'team', role: 'editor' }] },
      })
      expect(result.success).to.equal(true)
    })

    it('rejects an invalid principalType', () => {
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: [{ principalId: VALID_OID, principalType: 'group', role: 'editor' }] },
      })
      expect(result.success).to.equal(false)
    })

    it('defaults principalType to undefined (service-level default is "user") when omitted', () => {
      const result = upsertProjectMembersSchema.safeParse({
        params: { projectId: VALID_OID },
        body: { members: [{ principalId: VALID_OID, role: 'viewer' }] },
      })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.body.members[0]?.principalType).to.equal(undefined)
      }
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
    it('accepts valid projectId + memberUserId with no principalType query param', () => {
      const result = removeProjectMemberParamsSchema.safeParse({
        params: { projectId: VALID_OID, memberUserId: VALID_OID },
        query: {},
      })
      expect(result.success).to.equal(true)
    })

    it('accepts an explicit principalType=team query param', () => {
      const result = removeProjectMemberParamsSchema.safeParse({
        params: { projectId: VALID_OID, memberUserId: VALID_OID },
        query: { principalType: 'team' },
      })
      expect(result.success).to.equal(true)
      if (result.success) {
        expect(result.data.query.principalType).to.equal('team')
      }
    })

    it('rejects an invalid principalType query param', () => {
      const result = removeProjectMemberParamsSchema.safeParse({
        params: { projectId: VALID_OID, memberUserId: VALID_OID },
        query: { principalType: 'group' },
      })
      expect(result.success).to.equal(false)
    })

    it('rejects a malformed memberUserId', () => {
      const result = removeProjectMemberParamsSchema.safeParse({
        params: { projectId: VALID_OID, memberUserId: 'bad' },
        query: {},
      })
      expect(result.success).to.equal(false)
    })
  })
})
