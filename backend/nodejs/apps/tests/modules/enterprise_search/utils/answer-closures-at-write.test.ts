import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { appendMessages, updateMessageById } from '../../../../src/modules/enterprise_search/utils/utils'
import {
  configureVisibility,
  rememberAnswerClosures,
  resetVisibility,
  type ReaderRow,
  type VisibilityDeps,
} from '../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { labelContentHash } from '../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { memoryClosureStore, type Row } from '../services/conversation/privacy/closure-world'
import { Logger } from '../../../../src/libs/services/logger.service'

/** The row writer keeps each answer version's closure as it stores the answer, and never fails a write for it. */

const DIRECT_KEYS = ['records', 'vrids', 'nodes', 'saVrids', 'personal', 'agents', 'chat', 'projects', 'marks']
const ORG = new mongoose.Types.ObjectId()
const SESSION = new mongoose.Types.ObjectId()
const DANA = new mongoose.Types.ObjectId()

const label = (records: string[], parents: unknown[]): Record<string, unknown> => ({
  v: 5,
  ver: '',
  contentHash: '',
  direct: { ...Object.fromEntries(DIRECT_KEYS.map((k) => [k, []])), records },
  parents,
  complete: true,
  tagset: 1,
  pub: false,
})

/** An earlier answer already stored, with its label sealed to its version and text, and its closure kept. */
const earlier = async (store: Map<string, Row>): Promise<Row> => {
  const labelVer = new mongoose.Types.ObjectId().toString()
  const content = 'R says revenue grew.'
  const row: Row = {
    _id: new mongoose.Types.ObjectId().toString(),
    sessionId: SESSION.toString(),
    orgId: ORG.toString(),
    messageType: 'bot_response',
    requestedBy: DANA.toString(),
    content,
    parts: [],
    labelVer,
    provenance: { ...label(['rec-r'], []), ver: labelVer, contentHash: labelContentHash(content, []) },
  }
  store.set(String(row._id), row)
  await rememberAnswerClosures(ORG.toString(), [row])
  return row
}

const wire = (rows: Map<string, Row>, kept: ReturnType<typeof memoryClosureStore>): void => {
  const deps: Partial<VisibilityDeps> = {
    labels: async (_org, ids) => new Map(ids.flatMap((id) => (rows.has(id) ? [[id, rows.get(id) as ReaderRow]] : []))),
    store: new VerdictStore(),
    closures: kept,
  }
  configureVisibility(deps)
}

describe('the row writer keeps the closure of each answer it stores', () => {
  afterEach(() => {
    sinon.restore()
    resetVisibility()
  })

  it('appendMessages: the new answer has its closure kept, built from the kept closure of the answer it was given', async () => {
    const rows = new Map<string, Row>()
    const kept = memoryClosureStore(rows)
    wire(rows, kept)
    const parent = await earlier(rows)
    const inserted = new mongoose.Types.ObjectId()
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq: 2, rev: 2 } as never)
    const insertMany = sinon.stub(ChatSessionMessage, 'insertMany').resolves([{ _id: inserted }] as never)
    await appendMessages(SESSION, ORG, [
      {
        messageType: 'bot_response',
        content: 'So the plan holds.',
        requestedBy: DANA,
        provenance: label([], [{ id: parent._id, ver: parent.labelVer, mode: 'full' }]),
      } as never,
    ])
    const written = (insertMany.firstCall.args[0] as unknown as Array<{ labelVer: string }>)[0] as { labelVer: string }
    const doc = kept.docs.get(`${inserted.toString()}@${written.labelVer}`)
    expect(doc, 'kept at the write').to.not.equal(undefined)
    expect((doc?.chain as Array<{ key: string }>).map((c) => c.key)).to.deep.equal([`${String(parent._id)}@${String(parent.labelVer)}`])
  })

  it('updateMessageById: a regenerated answer has the closure of its new version kept', async () => {
    const rows = new Map<string, Row>()
    const kept = memoryClosureStore(rows)
    wire(rows, kept)
    const parent = await earlier(rows)
    const existing = { _id: new mongoose.Types.ObjectId(), sessionId: SESSION, orgId: ORG, seq: 4, messageType: 'bot_response', labelVer: new mongoose.Types.ObjectId().toString() }
    sinon.stub(ChatSessionMessage, 'findOne').resolves(existing as never)
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ rev: 5 } as never)
    const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ ...existing } as never)
    await updateMessageById(existing._id, ORG, {
      messageType: 'bot_response',
      content: 'Regenerated.',
      requestedBy: DANA,
      provenance: label(['rec-s'], [{ id: parent._id, ver: parent.labelVer, mode: 'text' }]),
    } as never)
    const written = replace.firstCall.args[1] as unknown as { labelVer: string }
    expect(kept.docs.has(`${existing._id.toString()}@${written.labelVer}`)).to.equal(true)
  })

  it('a closure store that fails never fails the write, and says so once: the first read makes the closure', async () => {
    const rows = new Map<string, Row>()
    const kept = memoryClosureStore(rows)
    wire(rows, {
      ...kept,
      put: async () => {
        throw new Error('store down')
      },
    } as never)
    const warn = sinon.stub(Logger.prototype, 'warn')
    const inserted = new mongoose.Types.ObjectId()
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq: 1, rev: 1 } as never)
    sinon.stub(ChatSessionMessage, 'insertMany').resolves([{ _id: inserted }] as never)
    const out = await appendMessages(SESSION, ORG, [
      { messageType: 'bot_response', content: 'Hello.', requestedBy: DANA, provenance: label([], []) } as never,
    ])
    expect(out).to.have.length(1)
    expect(kept.docs.size).to.equal(0)
    const said = warn.getCalls().filter((c) => c.args[0].startsWith('The answer closure store did not answer'))
    expect(said).to.have.length(1)
    expect(said[0]?.args[1]).to.include({ call: 'put', error: 'store down' })
  })

  it('with no store wired (the default, or PostgreSQL) a write reads and keeps nothing more', async () => {
    resetVisibility()
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq: 1, rev: 1 } as never)
    sinon.stub(ChatSessionMessage, 'insertMany').resolves([{ _id: new mongoose.Types.ObjectId() }] as never)
    const out = await appendMessages(SESSION, ORG, [
      { messageType: 'bot_response', content: 'Hello.', requestedBy: DANA, provenance: label([], []) } as never,
    ])
    expect(out).to.have.length(1)
  })
})
