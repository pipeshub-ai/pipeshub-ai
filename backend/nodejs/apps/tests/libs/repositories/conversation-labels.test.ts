import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg'
import { MongoConversationRepository } from '../../../src/libs/repositories/conversation/mongo'
import { MESSAGE_LABEL_FIELDS } from '../../../src/libs/repositories/conversation/message-label-fields'
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { messageDto, type MessageSqlRow } from '../../../src/libs/repositories/conversation/pg-rows'
import { messageRow } from '../../../src/libs/repositories/conversation/pg-import'
import { chatSessionMessages } from '../../../src/scripts/docstore-migrate/mapping/conversation'
import { labelContentHash } from '../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { LEGACY_LABEL_VER } from '../../../src/modules/enterprise_search/services/conversation/privacy/label.types'
import type { NewMessage } from '../../../src/libs/repositories/conversation/types'
import { InMemoryChatStore } from '../../modules/enterprise_search/controller/chat-test-harness'

type Row = Record<string, unknown>

const ORG = 'a'.repeat(24)
const SESSION = 'b'.repeat(24)

const LABEL = {
  v: 5,
  ver: '',
  contentHash: '',
  direct: { records: ['rec-CANARY-R'], vrids: [], nodes: [], saVrids: [], personal: [], agents: [], chat: [], projects: [], marks: [] },
  parents: [],
  complete: true,
  tagset: 1,
  pub: false,
}

const labelled = (content: string): NewMessage =>
  ({ messageType: 'bot_response', content, parts: [{ type: 'text', content }], provenance: LABEL }) as unknown as NewMessage

/**
 * A PostgreSQL unit that keeps message rows in memory: it answers the repository's own statements by reading their
 * column lists, so the test sees exactly which columns each statement writes and returns.
 */
class FakeKernel {
  readonly orgId = ORG
  readonly rows = new Map<string, Row>()
  readonly statements: Array<{ text: string; params: unknown[] }> = []
  private nextSeq = 0

  async query(text: string, params: unknown[] = []): Promise<{ rows: Row[]; rowCount: number }> {
    this.statements.push({ text, params })
    const tag = /pipeshub:conversation\.(\w+)/.exec(text)?.[1]
    if (tag === 'allocate_seq') {
      this.nextSeq += params[2] as number
      return { rows: [{ next_seq: this.nextSeq }], rowCount: 1 }
    }
    if (tag === 'insert_message') {
      const cols = (/\(([^)]*)\)\s*VALUES/.exec(text)?.[1] ?? '').split(',').map((c) => c.trim())
      const row: Row = {}
      cols.forEach((c, i) => (row[c] = this.decode(text, i + 1, params[i])))
      this.rows.set(row.id as string, row)
      return { rows: [this.returned(text, row)], rowCount: 1 }
    }
    if (tag === 'message_position') {
      const row = this.rows.get(params[1] as string)
      return { rows: row ? [row] : [], rowCount: row ? 1 : 0 }
    }
    if (tag === 'replace_message') {
      const row = this.rows.get(params[1] as string)
      if (!row) return { rows: [], rowCount: 0 }
      for (const [, col, n] of text.matchAll(/(\w+) = \$(\d+)/g)) {
        row[col as string] = this.decode(text, Number(n), params[Number(n) - 1])
      }
      return { rows: [this.returned(text, row)], rowCount: 1 }
    }
    throw new Error(`unexpected statement: ${text.slice(0, 80)}`)
  }

  private decode(text: string, n: number, value: unknown): unknown {
    return new RegExp(`\\$${n}::jsonb`).test(text) && typeof value === 'string' ? JSON.parse(value) : value
  }

  /** What `RETURNING` names, and nothing else. */
  private returned(text: string, row: Row): Row {
    const list = /RETURNING ([\s\S]*)$/.exec(text)?.[1] ?? ''
    const out: Row = {}
    for (const col of list.split(',').map((c) => c.trim().replace(/^m\./, ''))) out[col] = row[col] ?? null
    return out
  }
}

const unitOf = (kernel: FakeKernel) => ({ pgTx: { kernel } }) as never

describe('conversation repository: answer labels', () => {
  afterEach(() => sinon.restore())

  describe('PostgreSQL', () => {
    it('stores the label of an answer in its own columns, bound to the version and text it was written with', async () => {
      const kernel = new FakeKernel()
      const [dto] = await new PgConversationRepository().appendMessages(SESSION, ORG, [labelled('Revenue grew 12%.')], unitOf(kernel))
      const row = [...kernel.rows.values()][0] as Row
      expect(row.label_ver).to.match(/^[0-9a-f]{24}$/)
      const provenance = row.provenance as Row
      expect(provenance.ver).to.equal(row.label_ver)
      expect(provenance.contentHash).to.equal(labelContentHash('Revenue grew 12%.', row.parts))
      expect((provenance.direct as Row).records).to.deep.equal(['rec-CANARY-R'])
      expect(dto?.labelVer).to.equal(row.label_ver)
      expect(dto).to.not.have.any.keys('provenance', 'labelHistory')
    })

    it('the hash is the same one the document store takes, though this store drops a NUL', async () => {
      const kernel = new FakeKernel()
      await new PgConversationRepository().appendMessages(SESSION, ORG, [labelled('a\u0000b')], unitOf(kernel))
      const row = [...kernel.rows.values()][0] as Row
      expect(row.content).to.equal('ab')
      expect((row.provenance as Row).contentHash).to.equal(labelContentHash('a\u0000b', [{ type: 'text', content: 'a\u0000b' }]))
    })

    it('no statement returns the label columns', async () => {
      const kernel = new FakeKernel()
      const repo = new PgConversationRepository()
      const [dto] = await repo.appendMessages(SESSION, ORG, [labelled('One.')], unitOf(kernel))
      await repo.replaceMessage(dto?._id as string, ORG, labelled('Two.'), unitOf(kernel))
      for (const { text } of kernel.statements) {
        const returning = /RETURNING ([\s\S]*)$/.exec(text)?.[1] ?? ''
        expect(returning, text).to.not.match(/provenance|label_history/)
      }
    })

    it('regenerating an answer archives the label of the version it replaces, in the same statement', async () => {
      const kernel = new FakeKernel()
      const repo = new PgConversationRepository()
      const [first] = await repo.appendMessages(SESSION, ORG, [labelled('One.')], unitOf(kernel))
      const before = { ...(kernel.rows.get(first?._id as string) as Row) }
      const replaced = await repo.replaceMessage(first?._id as string, ORG, labelled('Two.'), unitOf(kernel))
      const row = kernel.rows.get(first?._id as string) as Row
      expect(row.label_history).to.deep.equal([{ ver: before.label_ver, provenance: before.provenance }])
      expect(row.label_ver).to.not.equal(before.label_ver)
      expect((row.provenance as Row).ver).to.equal(row.label_ver)
      expect(replaced).to.not.have.any.keys('provenance', 'labelHistory')
      expect(kernel.statements.filter((s) => s.text.includes('replace_message'))).to.have.length(1)
    })

    it('regenerating an answer written before labels archives the legacy version', async () => {
      const kernel = new FakeKernel()
      const repo = new PgConversationRepository()
      const [first] = await repo.appendMessages(SESSION, ORG, [{ messageType: 'bot_response', content: 'Old.' } as NewMessage], unitOf(kernel))
      const row = kernel.rows.get(first?._id as string) as Row
      row.label_ver = null
      await repo.replaceMessage(first?._id as string, ORG, labelled('New.'), unitOf(kernel))
      expect(row.label_history).to.deep.equal([{ ver: LEGACY_LABEL_VER }])
    })

    it('a row read back never carries its label, even when the columns are read', () => {
      const dto = messageDto(
        {
          id: 'c'.repeat(24), session_id: SESSION, seq: 1, message_type: 'bot_response', content: 'x', content_format: 'MARKDOWN',
          status: null, confidence: null, citations: [], follow_up_questions: [], feedback: [], metadata: null, model_info: null,
          applied_filters: null, attachments: [], reference_data: [], tools: [], reasoning: [], parts: [], label_ver: 'd'.repeat(24),
          provenance: LABEL, label_history: [{ ver: 'legacy' }], created_at: new Date(), updated_at: new Date(),
        } as unknown as MessageSqlRow,
        ORG,
      ).dto
      expect(dto).to.not.have.any.keys('provenance', 'labelHistory')
      expect((dto as unknown as Row).labelVer).to.equal('d'.repeat(24))
    })

    it('the copy from the document store moves the label fields whole', () => {
      const doc = {
        _id: 'c'.repeat(24), orgId: ORG, sessionId: SESSION, seq: 1, messageType: 'bot_response', content: 'x',
        labelVer: 'd'.repeat(24), provenance: { ...LABEL, ver: 'd'.repeat(24) }, labelHistory: [{ ver: 'legacy' }], createdAt: new Date(),
      }
      const row = messageRow(doc as never) as Row
      expect(row.label_ver).to.equal('d'.repeat(24))
      expect(row.provenance).to.deep.equal(doc.provenance)
      expect(row.label_history).to.deep.equal([{ ver: 'legacy' }])
      const mapped = chatSessionMessages.fields.map((f) => f.path)
      expect(mapped).to.include.members(['labelVer', 'provenance', 'labelHistory'])
    })
  })

  describe('MongoDB', () => {
    it('stores the label and returns the rows it wrote without it', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const session = store.addSession({ orgId: new mongoose.Types.ObjectId(), userId: new mongoose.Types.ObjectId(), initiator: new mongoose.Types.ObjectId() })
      const repo = new MongoConversationRepository()
      const [dto] = await repo.appendMessages(String(session._id), String(session.orgId), [labelled('Revenue grew.')])
      expect(dto).to.not.have.any.keys('provenance', 'labelHistory')
      const stored = store.messagesOf(session._id)[0] as Row
      expect((stored.provenance as Row).ver).to.equal(stored.labelVer)
      expect(dto?.labelVer).to.equal(stored.labelVer)
    })

    it('a regenerated answer keeps the label it replaced, and the write returns neither', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const session = store.addSession({ orgId: new mongoose.Types.ObjectId(), userId: new mongoose.Types.ObjectId(), initiator: new mongoose.Types.ObjectId() })
      const repo = new MongoConversationRepository()
      const [first] = await repo.appendMessages(String(session._id), String(session.orgId), [labelled('One.')])
      const before = { ...(store.messagesOf(session._id)[0] as Row) }
      const replaced = await repo.replaceMessage(first?._id as string, String(session.orgId), labelled('Two.'))
      expect(replaced).to.not.have.any.keys('provenance', 'labelHistory')
      const after = store.messagesOf(session._id)[0] as Row
      expect(after.labelHistory).to.deep.equal([{ ver: before.labelVer, provenance: before.provenance }])
      expect((after.provenance as Row).ver).to.equal(after.labelVer)
    })

    it('the reader check reads the label, the text and steps its hash covers and who asked, never the sources, tool output or files', async () => {
      const projections: unknown[] = []
      const query = {
        select(projection: unknown) {
          projections.push(projection)
          return query
        },
        lean: () => query,
        exec: async () => [],
      }
      sinon.stub(ChatSessionMessage, 'find').returns(query as never)
      await new MongoConversationRepository().listMessageLabels(ORG, [SESSION])
      expect(projections).to.have.length(1)
      const tokens = String(projections[0]).split(/\s+/).filter(Boolean)
      // An inclusion projection: a row comes back with the fields it names and nothing else.
      expect(tokens.some((t) => !/^[+-]/.test(t)), 'names the fields it returns').to.equal(true)
      expect(tokens.filter((t) => t.startsWith('-'))).to.deep.equal([])
      const names = tokens.map((t) => t.replace(/^\+/, ''))
      expect(names).to.have.members([...MESSAGE_LABEL_FIELDS])
      expect(names).to.include.members(['content', 'parts', 'labelVer', 'provenance', 'labelHistory', 'requestedBy', 'authorUserId', 'sessionId'])
      for (const unread of ['citations', 'referenceData', 'attachments', 'reasoning', 'tools', 'feedback', 'followUpQuestions', 'modelInfo', 'mentions', 'shareRequests']) {
        expect(names, unread).to.not.include(unread)
      }
    })
  })
})
