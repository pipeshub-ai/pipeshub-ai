import 'reflect-metadata'
import { expect } from 'chai'
import { z } from 'zod'
import {
  attachmentUploadSchema,
  conversationIdParamsSchema,
  conversationProjectLinkSchema,
  conversationShareParamsSchema,
  messageIdParamsSchema,
  searchIdParamsSchema,
} from '../../../src/modules/enterprise_search/validators/es_validators'
import {
  projectIdParamsSchema,
  removeProjectMemberParamsSchema,
  upsertProjectMembersSchema,
} from '../../../src/modules/projects/validators/project.validators'
import { markReadSchema } from '../../../src/modules/notification/validators/notification.validators'
import {
  getUserTeamsQuerySchema,
  teamIdParamsSchema,
} from '../../../src/modules/user_management/validators/teams.request.validators'

const OID = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const MIXED = 'aAbBcC0123456789abcdef01'
const UPPER = 'AAAAAAAAAAAAAAAAAAAAAAAA'
const BAD = ['xyz', 'a'.repeat(23), 'a'.repeat(25), `${OID.slice(0, 23)}g`]

type Case = { name: string; schema: z.ZodTypeAny; build: (v: string) => unknown; message: string }

const ES_PROJECT_ID = OID
const cases: Case[] = [
  { name: 'es conversationId param', schema: conversationIdParamsSchema, build: (v) => ({ params: { conversationId: v } }), message: 'Invalid conversation ID format' },
  { name: 'es messageId param', schema: messageIdParamsSchema, build: (v) => ({ params: { messageId: v } }), message: 'Invalid message ID format' },
  { name: 'es searchId param', schema: searchIdParamsSchema, build: (v) => ({ params: { searchId: v } }), message: 'Invalid search ID format' },
  {
    name: 'es share userIds',
    schema: conversationShareParamsSchema,
    build: (v) => ({ params: { conversationId: OID }, body: { userIds: [v] } }),
    message: 'Invalid user ID format',
  },
  {
    name: 'es project link projectId',
    schema: conversationProjectLinkSchema,
    build: (v) => ({ params: { conversationId: ES_PROJECT_ID }, body: { projectId: v } }),
    message: 'Invalid project ID format',
  },
  { name: 'es attachment upload conversationId', schema: attachmentUploadSchema, build: (v) => ({ body: { conversationId: v } }), message: 'Invalid conversation ID format' },
  { name: 'project projectId param', schema: projectIdParamsSchema, build: (v) => ({ params: { projectId: v } }), message: 'Invalid project ID format' },
  {
    name: 'project member principalId',
    schema: upsertProjectMembersSchema,
    build: (v) => ({ params: { projectId: OID }, body: { members: [{ principalId: v, role: 'viewer' }] } }),
    message: 'Invalid principal ID format',
  },
  {
    name: 'project member memberUserId',
    schema: removeProjectMemberParamsSchema,
    build: (v) => ({ params: { projectId: OID, memberUserId: v }, query: {} }),
    message: 'Invalid member ID format',
  },
  { name: 'notification id param', schema: markReadSchema, build: (v) => ({ params: { id: v } }), message: 'Invalid notification id' },
  { name: 'teams created_by', schema: getUserTeamsQuerySchema, build: (v) => ({ query: { created_by: v } }), message: 'Invalid user ID format' },
]

const messagesOf = (schema: z.ZodTypeAny, input: unknown): string[] | null => {
  const r = schema.safeParse(input)
  return r.success ? null : r.error.issues.map((i) => i.message)
}

describe('PH02-08 migrated ObjectId validators keep their behaviour and messages', () => {
  for (const c of cases) {
    describe(c.name, () => {
      it('accepts lowercase, mixed-case and uppercase 24-hex ids', () => {
        for (const v of [OID, MIXED, UPPER]) {
          const result = messagesOf(c.schema, c.build(v))
          // Unrelated required fields may fail; only the id message must be absent.
          expect(result ?? [], `${v}`).to.not.include(c.message)
        }
      })

      it('rejects non-hex, 23-char and 25-char ids with the exact message', () => {
        for (const v of BAD) {
          expect(messagesOf(c.schema, c.build(v)), v).to.deep.equal([c.message])
        }
      })
    })
  }

  it('es attachment upload still allows an empty and a whitespace-padded id', () => {
    expect(messagesOf(attachmentUploadSchema, { body: { conversationId: '' } })).to.equal(null)
    expect(messagesOf(attachmentUploadSchema, { body: { conversationId: `  ${OID} ` } })).to.equal(null)
    expect(messagesOf(attachmentUploadSchema, { body: { conversationId: null } })).to.equal(null)
  })

  it('es project link still accepts null', () => {
    expect(messagesOf(conversationProjectLinkSchema, { params: { conversationId: OID }, body: { projectId: null } })).to.equal(null)
  })

  describe('team id', () => {
    const ok = ['123e4567-e89b-42d3-a456-426614174000', `all_${OID}`, `all_${UPPER}`]
    const bad = ['all_x', 'all_', `all_${'a'.repeat(23)}`, `all_${'a'.repeat(25)}`, 'xyz', OID, '123e4567-e89b-92d3-a456-426614174000']
    it('accepts UUID and all_<24hex>', () => {
      for (const v of ok) expect(messagesOf(teamIdParamsSchema, { params: { teamId: v } }), v).to.equal(null)
    })
    it('rejects the rest with the exact message', () => {
      for (const v of bad) expect(messagesOf(teamIdParamsSchema, { params: { teamId: v } }), v).to.deep.equal(['Invalid team ID format'])
    })
  })
})
