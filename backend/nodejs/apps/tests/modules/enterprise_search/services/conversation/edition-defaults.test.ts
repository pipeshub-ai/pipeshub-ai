import 'reflect-metadata'
import { expect } from 'chai'
import { z } from 'zod'
import { Schema } from 'mongoose'
import { defaultConversationEdition } from '../../../../../src/modules/enterprise_search/services/conversation/conversation-edition'
import {
  conversationExtensions,
  useConversationExtensions,
} from '../../../../../src/modules/enterprise_search/services/conversation/conversation-extensions'
import { defaultTurnExtension } from '../../../../../src/modules/enterprise_search/services/conversation/turn/default-turn-extension'
import { RecordPermissionChatContentAccess } from '../../../../../src/modules/enterprise_search/services/conversation/sharing/chat-content-access'
import { withBodyFields } from '../../../../../src/modules/enterprise_search/validators/es_validators'
import { noSchemaExtensions } from '../../../../../src/modules/enterprise_search/schema/schema-extensions'
import { mergeSpecFragment, useSpecFragment, withSpecFragments } from '../../../../../src/modules/api-docs/spec-fragments'
import { getEmailContent, useEmailTemplates } from '../../../../../src/modules/mail/utils/email-content'
import { noNotificationEmails } from '../../../../../src/modules/notification/service/notification-email.port'

describe('conversation edition defaults', () => {
  afterEach(() => useConversationExtensions(undefined))

  it('the default edition exposes no extra headers, binds nothing and mounts no routes', () => {
    expect(defaultConversationEdition.exposedHeaders).to.deep.equal([])
    expect(defaultConversationEdition.bind({} as never, {} as never, {} as never)).to.equal(undefined)
    expect(defaultConversationEdition.mountRoutes({} as never, {} as never)).to.equal(undefined)
  })

  it('the default extensions answer as a single-author conversation', async () => {
    const ext = conversationExtensions()
    expect(ext.turns).to.equal(defaultTurnExtension)
    expect(ext.content).to.be.instanceOf(RecordPermissionChatContentAccess)
    expect(ext.acl.versionBump()).to.deep.equal({})
    expect(ext.routes.requestFields('firstSend')).to.deep.equal({})
    expect(ext.routes.firstSend()).to.deep.equal([])
    expect(ext.lists.select({} as never)).to.equal('-__v')
    expect(ext.archives.decide({} as never, { isArchived: true } as never, 'u', 'unarchive')).to.deep.equal({ archived: true })
    expect(ext.views.displayTitle('t' as never, {} as never)).to.equal('t')
    expect(await ext.lifecycle.userDeleted('o', 'u', {} as never)).to.deep.equal({})
  })

  it('the default turn extension answers with the target and adds nothing to the payload', async () => {
    const target = { kind: 'chat' } as never
    expect(defaultTurnExtension.responderFor({} as never, target)).to.deep.equal({ target })
    expect(await defaultTurnExtension.prepareFirstSend({ target } as never)).to.deep.equal({ responder: { target } })
    expect(defaultTurnExtension.aiPayloadFields({} as never)).to.deep.equal({})
    expect(defaultTurnExtension.toolCardFor('anything')).to.equal(undefined)
    expect(defaultTurnExtension.queryForValidation('q')).to.equal('q')
  })

  it('an override replaces only its area, and undefined restores every default', () => {
    const acl = { versionBump: () => ({ $inc: { v: 1 } }), bumpDocument: () => undefined }
    useConversationExtensions({ acl })
    expect(conversationExtensions().acl).to.equal(acl)
    expect(conversationExtensions().turns).to.equal(defaultTurnExtension)
    useConversationExtensions(undefined)
    expect(conversationExtensions().acl.versionBump()).to.deep.equal({})
  })
})

describe('withBodyFields', () => {
  const schema = z.object({ body: z.object({ query: z.string() }) })

  it('returns the schema itself when there are no fields', () => {
    expect(withBodyFields(schema, {})).to.equal(schema)
  })

  it('adds the fields to the body and keeps stripping unknown ones', () => {
    const extended = withBodyFields(schema, { extra: z.number().optional() })
    const parsed = extended.parse({ body: { query: 'q', extra: 1, other: true } })
    expect(parsed.body).to.deep.equal({ query: 'q', extra: 1 })
  })
})

describe('mergeSpecFragment', () => {
  it('merges objects key by key, replaces other values and removes null keys', () => {
    const base = { paths: { '/a': { get: { summary: 'a' } }, '/b': {} }, tags: ['x'] }
    const merged = mergeSpecFragment(base, { paths: { '/a': { post: { summary: 'p' } }, '/b': null }, tags: ['y'] })
    expect(merged).to.deep.equal({ paths: { '/a': { get: { summary: 'a' }, post: { summary: 'p' } } }, tags: ['y'] })
    expect(base.paths).to.have.property('/b')
  })

  it('ignores a registered fragment whose files do not exist', () => {
    useSpecFragment(['/nonexistent/fragment.yaml'])
    expect(withSpecFragments({ openapi: '3.0.0' })).to.deep.equal({ openapi: '3.0.0' })
  })
})

describe('extension defaults outside conversations', () => {
  it('a registered email template renders, and an unknown type still throws', () => {
    useEmailTemplates({ testOnlyTemplate: (data) => `hello ${String(data.name)}` })
    expect(getEmailContent('testOnlyTemplate', { name: 'Ada' })).to.equal('hello Ada')
    expect(() => getEmailContent('noSuchTemplate', {})).to.throw()
  })

  it('no schema extensions leave a schema unchanged', () => {
    const schema = new Schema({ a: String })
    noSchemaExtensions.chatSession(schema)
    noSchemaExtensions.chatSessionMessage(schema)
    noSchemaExtensions.project(schema)
    expect(Object.keys(schema.paths).sort()).to.deep.equal(['_id', 'a'])
  })

  it('the default email dispatcher sends nothing and never throws', async () => {
    await noNotificationEmails.dispatch({ orgId: 'o', assignedTo: 'u', emailIntent: { template: 't' } } as never)
  })
})
