import { expect } from 'chai'
import sinon from 'sinon'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
  REDACTED_TOKEN,
  collectMessageRecordIds,
  conversationForAuthorOnly,
  redactConversationFor,
  redactMessagesFor,
  redactSearchesFor,
  readableRecordIds,
  redactAnswerText,
  redactMessagesForViewer,
  redactSearchForViewer,
} from '../../../../src/modules/enterprise_search/utils/viewer-redaction'

const SECRET = 'Q3 revenue was 4.2M according to the board deck'
const cite = (n: number, recordId: string, extra: Record<string, any> = {}) => ({
  citationId: {
    _id: `cit-${n}`,
    chunkIndex: n,
    content: `chunk text of ${recordId}`,
    metadata: {
      recordId,
      recordName: `Secret name ${recordId}`,
      origin: 'UPLOAD',
      webUrl: `https://app/record/${recordId}`,
      blockText: `block text of ${recordId}`,
      ...extra,
    },
  },
  excerpt: `excerpt of ${recordId}`,
  context: `context of ${recordId}`,
})
const answer = (content: string, citations: any[], extra: Record<string, any> = {}) => ({
  _id: 'm1',
  messageType: 'bot_response',
  content,
  citations,
  tools: [{ toolName: 't', toolResult: SECRET }],
  reasoning: [{ content: SECRET }],
  followUpQuestions: [{ question: SECRET }],
  ...extra,
})

describe('viewer redaction (shared chats)', () => {
  describe('redactAnswerText', () => {
    it('replaces a sentence whose citations are all unreadable', () => {
      const out = redactAnswerText('Revenue grew 4%. [1] Costs fell. [2]', new Set([1]))
      expect(out).to.equal(`${REDACTED_TOKEN} Costs fell. [2]`)
    })

    it('keeps a mixed sentence and drops only the unreadable marker', () => {
      expect(redactAnswerText('Both agree [1][2].', new Set([1]))).to.equal('Both agree [2].')
    })

    it('handles markers before the period and double brackets', () => {
      const out = redactAnswerText('Secret fact [[1]]. Public fact [[2]].', new Set([1]))
      expect(out).to.equal(`${REDACTED_TOKEN} Public fact [[2]].`)
    })

    it('keeps list markers and redacts per bullet', () => {
      const out = redactAnswerText('- hidden [1]\n- shown [2]', new Set([1]))
      expect(out).to.equal(`- ${REDACTED_TOKEN}\n- shown [2]`)
    })

    it('redacts a table row and a fenced block cited only by unreadable sources', () => {
      const text = '| a | b [1] |\n|---|---|\n```\ncode [1]\n```'
      const out = redactAnswerText(text, new Set([1]))
      expect(out).to.not.include('code')
      expect(out).to.include(`| ${REDACTED_TOKEN} |`)
    })

    it('collapses neighbouring redactions', () => {
      expect(redactAnswerText('One [1]. Two [1]. Three [2].', new Set([1]))).to.equal(
        `${REDACTED_TOKEN} Three [2].`,
      )
    })

    it('does not split decimals', () => {
      expect(redactAnswerText('Margin is 4.5 percent [1].', new Set([1]))).to.equal(REDACTED_TOKEN)
    })
  })

  describe('redactMessagesForViewer', () => {
    it('T1: leaves a message untouched when every source is readable', () => {
      const msg = answer('Fact [1]. Other [2].', [cite(1, 'r1'), cite(2, 'r2')])
      const [out] = redactMessagesForViewer([msg], new Set(['r1', 'r2']))
      expect(out.content).to.equal(msg.content)
      expect(out.citations).to.equal(msg.citations)
      expect(out.redaction).to.equal(undefined)
      expect(out.detailsHidden).to.equal(true)
      expect(out.tools).to.equal(undefined)
      expect(out.reasoning).to.equal(undefined)
    })

    it('T2: redacts one unreadable citation and its sentence, keeps the rest, marks partial', () => {
      const msg = answer(`${SECRET} [1]. Public fact [2]. A closing remark.`, [cite(1, 'r1'), cite(2, 'r2')])
      const [out] = redactMessagesForViewer([msg], new Set(['r2']))
      expect(out.content).to.equal(`${REDACTED_TOKEN} Public fact [2]. A closing remark.`)
      expect(out.citations[0]).to.deep.equal({
        citationId: 'cit-1',
        redacted: true,
        reason: 'no_access',
        requestAccess: { recordId: 'r1', kind: 'native', endpoint: '/api/v1/access-requests' },
      })
      expect(out.citations[1].citationId.metadata.recordId).to.equal('r2')
      expect(out.redaction).to.deep.equal({ redacted: false, partial: true, reason: 'no_access' })
    })

    it('T3: drops the whole body when every citation is unreadable', () => {
      const msg = answer(`${SECRET} [1]. And more.`, [cite(1, 'r1'), cite(2, 'r2')])
      const [out] = redactMessagesForViewer([msg], new Set())
      expect(out.content).to.equal('')
      expect(out.redaction).to.deep.equal({ redacted: true, partial: false, reason: 'no_access' })
    })

    it('T5: a connector citation carries the source deep link, native ones do not', () => {
      const msg = answer('A [1]. B [2].', [
        cite(1, 'wiki', { origin: 'CONNECTOR', webUrl: 'https://wiki.example/p/1' }),
        cite(2, 'kb'),
      ])
      const [out] = redactMessagesForViewer([msg], new Set())
      expect(out.citations[0].requestAccess).to.deep.equal({
        recordId: 'wiki',
        kind: 'connector',
        sourceUrl: 'https://wiki.example/p/1',
        endpoint: '/api/v1/access-requests',
      })
      expect(out.citations[1].requestAccess.sourceUrl).to.equal(undefined)
    })

    it('T6: the serialized response never contains redacted text, names or derived data', () => {
      const msg = answer(`${SECRET} [1]. Public fact [2].`, [cite(1, 'r1'), cite(2, 'r2')], {
        attachments: [{ recordId: 'att', recordName: 'Secret attachment.pdf', mimeType: 'application/pdf' }],
        referenceData: [{ name: SECRET }],
        parts: [{ type: 'text', text: SECRET }],
      })
      const json = JSON.stringify(redactMessagesForViewer([msg], new Set(['r2'])))
      expect(json).to.not.include('Q3 revenue')
      for (const leak of ['Secret name r1', 'chunk text of r1', 'block text of r1', 'excerpt of r1', 'context of r1']) {
        expect(json).to.not.include(leak)
      }
      expect(json).to.include('chunk text of r2')
      expect(json).to.not.include('Secret attachment')
      expect(json).to.not.include('toolResult')
      expect(json).to.not.include('"reasoning":[{')
      expect(json).to.not.include('/record/r1')
    })

    it('flags a KB citation as a collection request without disclosing the collection name', () => {
      const msg = answer('A [1].', [cite(1, 'kb', { connector: 'KB', connectorInstanceName: 'Finance-SECRET-NAME' })])
      const [out] = redactMessagesForViewer([msg], new Set())
      expect(out.citations[0].requestAccess.collection).to.equal(true)
      expect(JSON.stringify(out)).to.not.include('Finance-SECRET-NAME')
      const [other] = redactMessagesForViewer([answer('A [1].', [cite(1, 'x', { connector: 'DRIVE', connectorInstanceName: 'Team Drive' })])], new Set())
      expect(other.citations[0].requestAccess.collection).to.equal(undefined)
    })

    it('fails closed for a marker with no matching citation', () => {
      const msg = answer('Ghost claim [9]. Real [1].', [cite(1, 'r1'), cite(2, 'r2')])
      const [out] = redactMessagesForViewer([msg], new Set(['r1']))
      expect(out.content).to.equal(`${REDACTED_TOKEN} Real [1].`)
    })

    it('redacts an unreadable attachment without its name, and flags no partial answer on a question', () => {
      const msg = { _id: 'u1', messageType: 'user_query', content: 'see file', attachments: [{ recordId: 'att', recordName: 'secret.pdf' }] }
      const [out] = redactMessagesForViewer([msg], new Set())
      expect(out.attachments[0].redacted).to.equal(true)
      expect(out.redaction).to.equal(undefined)
      expect(out.content).to.equal('see file')
      expect(JSON.stringify(out)).to.not.include('secret.pdf')
    })

    it('collects citation and attachment record ids for one batched check', () => {
      const ids = collectMessageRecordIds([
        answer('x [1]', [cite(1, 'r1')]),
        { attachments: [{ recordId: 'att' }] },
      ])
      expect(ids).to.deep.equal(['r1', 'att'])
    })
  })

  describe('every path that returns messages to a non-author', () => {
    afterEach(() => sinon.restore())
    const config: any = { jwtSecret: 's', scopedJwtSecret: 's2', aiBackend: 'http://ai' }
    const viewer = { userId: 'viewer', orgId: 'o' }
    // Shape produced by attachPopulatedCitations (complete / regenerate frames).
    const populatedShape = (n: number, recordId: string) => {
      const { citationId: doc, ...rest } = cite(n, recordId)
      return { ...rest, citationId: doc._id, citationData: doc }
    }

    it('redacts a conversation object as sent in complete frames', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { accessible: ['r2'] } } as any)
      const conversation = {
        initiator: 'author',
        messages: [answer(`${SECRET} [1]. Public [2].`, [populatedShape(1, 'r1'), populatedShape(2, 'r2')])],
      }
      const out = await redactConversationFor(config, conversation, viewer)
      const json = JSON.stringify(out)
      expect(json).to.not.include('Q3 revenue')
      expect(json).to.not.include('chunk text of r1')
      expect(json).to.not.include('toolResult')
      expect(json).to.include('chunk text of r2')
      expect(out.messages[0].citations[0].redacted).to.equal(true)
    })

    it('leaves the author’s own conversation alone and makes no call', async () => {
      const exec = sinon.stub(AIServiceCommand.prototype, 'execute')
      const conversation = { initiator: 'viewer', messages: [answer('x [1]', [cite(1, 'r1')])] }
      expect(await redactConversationFor(config, conversation, viewer)).to.equal(conversation)
      expect(exec.called).to.equal(false)
    })

    it('redacts model history for a write-shared viewer, failing closed if the check is down', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('down'))
      const out = await redactMessagesFor(config, [answer(`${SECRET} [1].`, [cite(1, 'r1')])], 'author', viewer)
      expect(JSON.stringify(out)).to.not.include('Q3 revenue')
      expect(out[0].content).to.equal('')
    })

    it('error frames carry the conversation only for its author', () => {
      const conversation = { initiator: 'author', messages: [answer('x', [])] }
      expect(conversationForAuthorOnly(conversation, 'author')).to.equal(conversation)
      expect(conversationForAuthorOnly(conversation, 'viewer')).to.equal(undefined)
      expect(conversationForAuthorOnly(conversation, undefined)).to.equal(undefined)
    })
  })

  describe('QA-W7-1: saved search list', () => {
    afterEach(() => sinon.restore())
    const config: any = { jwtSecret: 's', scopedJwtSecret: 's2', aiBackend: 'http://ai' }
    const viewer = { userId: 'viewer', orgId: 'o' }
    const stored = () => ({
      _id: 's1',
      userId: 'author',
      query: 'q',
      // History rows hold bare citation ids, plus a records map that can name uncited records.
      citationIds: ['c1', 'c2'],
      records: { r1: 'SEARCHREC-SECRET-KAPPA', r2: 'public', r9: 'SEARCHREC-WIKI-LAMBDA' },
    })
    const docs = [
      { _id: 'c1', content: 'chunk text of r1', metadata: { recordId: 'r1', origin: 'UPLOAD', recordName: 'Secret name r1' } },
      { _id: 'c2', content: 'ok', metadata: { recordId: 'r2', origin: 'UPLOAD' } },
    ]

    it('redacts hits and the records map of a shared search, with no leak in the raw JSON', async () => {
      sinon.stub(Citation, 'find').returns({ select: () => ({ lean: async () => docs }) } as any)
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { accessible: ['r2'] } } as any)
      const out = await redactSearchesFor(config, [stored()], viewer)
      const json = JSON.stringify(out)
      for (const leak of ['SEARCHREC-SECRET-KAPPA', 'SEARCHREC-WIKI-LAMBDA', 'chunk text of r1', 'Secret name r1']) {
        expect(json).to.not.include(leak)
      }
      expect(out[0].citationIds[0].redacted).to.equal(true)
      expect(out[0].citationIds[1].content).to.equal('ok')
      expect(out[0].records).to.deep.equal({ r2: 'public' })
    })

    it('leaves the author’s own searches untouched and makes no call', async () => {
      const exec = sinon.stub(AIServiceCommand.prototype, 'execute')
      const own = { ...stored(), userId: 'viewer' }
      expect((await redactSearchesFor(config, [own], viewer))[0]).to.equal(own)
      expect(exec.called).to.equal(false)
    })

    it('treats a hit whose document is missing as unreadable (fails closed)', async () => {
      sinon.stub(Citation, 'find').returns({ select: () => ({ lean: async () => [] }) } as any)
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('down'))
      const out = await redactSearchesFor(config, [stored()], viewer)
      expect(out[0].citationIds.every((c: any) => c.redacted)).to.equal(true)
      expect(out[0].records).to.deep.equal({})
    })
  })

  describe('T8: redactSearchForViewer', () => {
    const search = {
      _id: 's1',
      userId: 'author',
      query: 'q',
      records: { r1: 'Secret name r1', r2: 'Public' },
      citationIds: [
        { _id: 'c1', content: SECRET, metadata: { recordId: 'r1', origin: 'UPLOAD', recordName: 'Secret name r1' } },
        { _id: 'c2', content: 'ok', metadata: { recordId: 'r2', origin: 'UPLOAD' } },
      ],
    }

    it('keeps readable hits and strips the rest', () => {
      const out = redactSearchForViewer(search, new Set(['r2']))
      expect(out.citationIds[0].redacted).to.equal(true)
      expect(out.citationIds[1].content).to.equal('ok')
      expect(out.records).to.deep.equal({ r2: 'Public' })
      expect(JSON.stringify(out)).to.not.include('Secret name')
      expect(JSON.stringify(out)).to.not.include('Q3 revenue')
    })

    it('returns the search untouched when everything is readable', () => {
      expect(redactSearchForViewer(search, new Set(['r1', 'r2']))).to.equal(search)
    })
  })

  describe('readableRecordIds', () => {
    afterEach(() => sinon.restore())
    const config: any = { jwtSecret: 's', scopedJwtSecret: 's2', aiBackend: 'http://ai' }

    it('asks Python once per batch as the viewer and returns the accessible ids', async () => {
      const exec = sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .resolves({ statusCode: 200, data: { accessible: ['r2'] } } as any)
      const out = await readableRecordIds(config, { userId: 'u', orgId: 'o' }, ['r1', 'r2', 'r2'])
      expect([...out]).to.deep.equal(['r2'])
      expect(exec.calledOnce).to.equal(true)
    })

    it('fails closed when the check errors', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('down'))
      expect((await readableRecordIds(config, { userId: 'u', orgId: 'o' }, ['r1'])).size).to.equal(0)
    })

    it('fails closed on a non-200', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 503, data: {} } as any)
      expect((await readableRecordIds(config, { userId: 'u', orgId: 'o' }, ['r1'])).size).to.equal(0)
    })

    it('makes no call without ids', async () => {
      const exec = sinon.stub(AIServiceCommand.prototype, 'execute')
      await readableRecordIds(config, { userId: 'u', orgId: 'o' }, [])
      expect(exec.called).to.equal(false)
    })
  })
})
