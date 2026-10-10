import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Flow, OWNER, ORG, finalAnswer, flows, startStream } from './streaming-flows'

const VALIDATE = /\/api\/v1\/chat\/attachments\/validate$/
const sendFlows = flows.filter((f) => !f.regenerate)

const withAttachments = (flow: Flow): Flow => ({
  ...flow,
  prepare: (store) => {
    const prepared = flow.prepare(store)
    return {
      ...prepared,
      body: { ...prepared.body, attachments: [{ recordId: 'r1', recordName: 'a.pdf' }, { recordId: 'kbRec' }] },
    }
  },
})

const storedAttachments = (run: Awaited<ReturnType<typeof startStream>>): unknown => {
  const userMsg = run.messages().filter((m) => m.messageType === 'user_query').at(-1)
  return userMsg?.attachments ?? []
}

const finish = async (run: Awaited<ReturnType<typeof startStream>>): Promise<void> => {
  run.ai.send('RUN_FINISHED', finalAnswer('ok'))
  run.ai.finish()
  await run.res.ended
}

describe('es_controller user-query attachment validation', () => {
  afterEach(() => {
    sinon.restore()
  })

  for (const flow of sendFlows) {
    describe(flow.name, () => {
      it('PH01-04 stores only the attachments the AI service validates', async () => {
        const run = await startStream(withAttachments(flow), undefined, (ai) => ai.reply(VALIDATE, 200, { recordIds: ['r1'] }))
        await finish(run)

        const call = run.ai.calls.find((c) => VALIDATE.test(c.url))
        expect(call?.body).to.deep.equal({ recordIds: ['r1', 'kbRec'] })
        expect(call?.headers.authorization).to.match(/^Bearer /)
        expect(storedAttachments(run)).to.deep.equal([{ recordId: 'r1', recordName: 'a.pdf' }])
      })

      it('sends the AI service the validated attachments, not the raw request list', async () => {
        const run = await startStream(withAttachments(flow), undefined, (ai) => ai.reply(VALIDATE, 200, { recordIds: ['r1'] }))
        await finish(run)

        const body = run.ai.streamCalls[0]?.body as { attachments: unknown }
        expect(body.attachments).to.deep.equal([{ recordId: 'r1', recordName: 'a.pdf' }])
      })

      it('PH01-04 B-1 keeps service-account (Slack bot) attachments and flags the validate token', async () => {
        const serviceUser = { userId: OWNER, orgId: ORG, email: 'bot@example.com', isServiceAccount: true }
        const run = await startStream(
          withAttachments(flow),
          undefined,
          (ai) => ai.reply(VALIDATE, 200, { recordIds: ['r1', 'kbRec'] }),
          serviceUser,
        )
        await finish(run)

        const call = run.ai.calls.find((c) => VALIDATE.test(c.url))
        const token = String(call?.headers.authorization).replace(/^Bearer /, '')
        expect(jwt.verify(token, 'test-scoped-secret')).to.include({ isServiceAccount: true })
        expect(storedAttachments(run)).to.have.length(2)
        const body = run.ai.streamCalls[0]?.body as { attachments: unknown[] }
        expect(body.attachments).to.have.length(2)
      })

      it('does not flag the validate token as a service account for a regular user', async () => {
        const run = await startStream(withAttachments(flow), undefined, (ai) => ai.reply(VALIDATE, 200, { recordIds: ['r1'] }))
        await finish(run)

        const call = run.ai.calls.find((c) => VALIDATE.test(c.url))
        const token = String(call?.headers.authorization).replace(/^Bearer /, '')
        expect(jwt.verify(token, 'test-scoped-secret')).to.not.have.property('isServiceAccount')
      })

      it('PH01-04 drops every attachment, and still sends, when validation fails', async () => {
        const run = await startStream(withAttachments(flow), undefined, (ai) =>
          ai.failNetwork(VALIDATE, new DOMException('timed out', 'TimeoutError')),
        )
        await finish(run)

        expect(storedAttachments(run)).to.deep.equal([])
        expect(run.ai.streamCalls).to.have.length(1)
        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
      })

      it('drops every attachment when validation answers with an error status', async () => {
        const run = await startStream(withAttachments(flow), undefined, (ai) => ai.reply(VALIDATE, 403, { detail: 'no' }))
        await finish(run)

        expect(storedAttachments(run)).to.deep.equal([])
      })

      it('does not call the validate route when there are no attachments', async () => {
        const run = await startStream(flow)
        await finish(run)

        expect(run.ai.calls.some((c) => VALIDATE.test(c.url))).to.equal(false)
      })
    })
  }
})
