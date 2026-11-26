import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { AgentRunModel } from '../../../../src/libs/repositories/agent-harness/execution/schema/execution.schema'
import { FOREIGN_TURN_STUB } from '../../../../src/modules/agent-harness/domain/session-audience'
import {
  detachCopiedRuns,
  forkedFromOf,
  withForeignRunsDetached,
} from '../../../../src/modules/enterprise_search/utils/copied-runs'
import { stubCollaboratorTurn } from '../../../../src/modules/enterprise_search/utils/fork-conversation'
import type { IMessage } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'

/** A reported fork: its answers kept the source's run ids, on the message and in its parts. */
const SOURCE_RUNS = ['cd04a9d1-7652-4ad0-af2c-b3d7792a195a', 'ddec62f5-d4b8-4566-8c60-2c8f1e469be9']
const CHILD_RUN = 'd2d96865-7dd0-e6e3-dec9-03a95dfab23e'
const reportedFork = (): IMessage[] =>
  [
    { messageType: 'user_query', content: 'Write a python program that prints first ten prime numbers and run it' },
    {
      messageType: 'bot_response',
      content: 'Here is the output',
      runId: SOURCE_RUNS[0],
      parts: [
        { type: 'text', runId: SOURCE_RUNS[0], text: 'Planning' },
        { type: 'tool_call', runId: SOURCE_RUNS[0], toolName: 'code_task', status: 'completed' },
        { type: 'sub_agent', runId: CHILD_RUN, parentRunId: SOURCE_RUNS[0], status: 'completed', parts: [{ type: 'text', runId: CHILD_RUN }] },
        { type: 'text', runId: SOURCE_RUNS[0], text: 'Done', isFinal: true },
      ],
    },
    { messageType: 'user_query', content: 'and the next ten?' },
    { messageType: 'bot_response', content: 'More primes', runId: SOURCE_RUNS[1], parts: [{ type: 'text', runId: SOURCE_RUNS[1] }] },
  ] as unknown as IMessage[]

/** Every run id a message still names, on itself or in any part. */
const runIdsIn = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.flatMap(runIdsIn)
  if (value === null || typeof value !== 'object') return []
  return Object.entries(value).flatMap(([key, item]) =>
    (key === 'runId' || key === 'parentRunId') && typeof item === 'string' ? [item] : runIdsIn(item),
  )
}

describe('FORKFIX: copied messages name no run of another conversation', () => {
  afterEach(() => sinon.restore())

  describe('detachCopiedRuns', () => {
    it('drops the message run id and every part run id, nested parts included, and marks it copied', () => {
      const [, answer] = reportedFork()
      const detached = detachCopiedRuns(answer!)
      expect(runIdsIn(detached)).to.deep.equal([])
      expect(detached.copied).to.equal(true)
      expect(detached.content).to.equal('Here is the output')
      expect(detached.parts!.map((part) => part.type)).to.deep.equal(['text', 'tool_call', 'sub_agent', 'text'])
      expect(detached.parts![2]).to.include({ status: 'completed' })
      expect((detached.parts![3] as { isFinal?: boolean }).isFinal).to.equal(true)
    })

    it('never mutates the message it was given', () => {
      const [, answer] = reportedFork()
      const before = JSON.stringify(answer)
      detachCopiedRuns(answer!)
      expect(JSON.stringify(answer)).to.equal(before)
    })

    it('keeps a part that is not an object as it is, and a message without parts without them', () => {
      const odd = detachCopiedRuns({ messageType: 'bot_response', content: 'x', runId: 'r', parts: ['raw' as never] } as IMessage)
      expect(odd.parts).to.deep.equal(['raw'])
      const bare = detachCopiedRuns({ messageType: 'user_query', content: 'q' } as IMessage)
      expect(bare).to.deep.equal({ messageType: 'user_query', content: 'q', copied: true })
    })
  })

  describe('forkedFromOf', () => {
    it('reads the fork marker from a document Map or a lean object, and nothing else', () => {
      expect(forkedFromOf(new Map([['forkedFromConversationId', 'abc']]))).to.equal('abc')
      expect(forkedFromOf({ forkedFromConversationId: 'abc' })).to.equal('abc')
      expect(forkedFromOf({ forkedFromConversationId: '' })).to.equal(null)
      expect(forkedFromOf(undefined)).to.equal(null)
      expect(forkedFromOf({ other: 'x' })).to.equal(null)
    })
  })

  describe('withForeignRunsDetached (forks written before FORKFIX)', () => {
    const orgId = new Types.ObjectId().toString()
    const forkId = new Types.ObjectId()
    const runsOfFork = (runIds: string[]) =>
      sinon.stub(AgentRunModel, 'find').returns({ lean: () => Promise.resolve(runIds.map((runId) => ({ runId }))) } as never)

    it('reproduces the three 404s: the fork answers carry no run id, so no run route is asked', async () => {
      const find = runsOfFork([])
      const served = await withForeignRunsDetached({ _id: forkId, metadata: { forkedFromConversationId: 'src' } }, orgId, reportedFork())
      expect(runIdsIn(served)).to.deep.equal([])
      expect(served.map((message) => message.copied === true)).to.deep.equal([false, true, false, true])
      expect(find.firstCall.args[0]).to.deep.equal({ orgId, sessionId: String(forkId) })
    })

    it("keeps the fork's own runs: only an answer naming another conversation's run is detached", async () => {
      runsOfFork(['own-run'])
      const own = { messageType: 'bot_response', content: 'new turn', runId: 'own-run', parts: [{ type: 'text', runId: 'own-run' }] } as unknown as IMessage
      const served = await withForeignRunsDetached({ _id: forkId, metadata: new Map([['forkedFromConversationId', 'src']]) }, orgId, [...reportedFork(), own])
      expect(served[4]).to.equal(own)
      expect(runIdsIn(served.slice(0, 4))).to.deep.equal([])
    })

    it('reads nothing for a conversation that is not a fork, or a fork whose messages name no run', async () => {
      const find = sinon.stub(AgentRunModel, 'find').throws(new Error('must not query'))
      const messages = reportedFork()
      expect(await withForeignRunsDetached({ _id: forkId }, orgId, messages)).to.equal(messages)
      const plain = [{ messageType: 'user_query', content: 'q' }] as IMessage[]
      expect(await withForeignRunsDetached({ _id: forkId, metadata: { forkedFromConversationId: 'src' } }, orgId, plain)).to.equal(plain)
      expect(find.called).to.equal(false)
    })

    it("leaves a legacy answer's protocol run ids in its parts: only the message's own run id is a harness run", async () => {
      runsOfFork([])
      const legacy = { messageType: 'bot_response', content: 'a', parts: [{ type: 'text', runId: 'agui-run' }] } as unknown as IMessage
      const served = await withForeignRunsDetached({ _id: forkId, metadata: { forkedFromConversationId: 'src' } }, orgId, [...reportedFork(), legacy])
      expect(served[4]).to.equal(legacy)
    })
  })

  describe("stubCollaboratorTurn (main's fork of a shared conversation)", () => {
    const owner = new Types.ObjectId().toString()
    const collaborator = new Types.ObjectId()

    it("keeps the owner's own turns, which carry no sender", () => {
      const turn = { messageType: 'bot_response', content: 'mine' } as IMessage
      expect(stubCollaboratorTurn(turn, owner)).to.equal(turn)
      const named = { messageType: 'bot_response', content: 'mine', senderId: owner } as unknown as IMessage
      expect(stubCollaboratorTurn(named, owner)).to.equal(named)
    })

    it("replaces a collaborator's turn with the continue-privately stub: no text, citations, parts or sender", () => {
      const turn = {
        messageType: 'bot_response',
        content: 'SECRET answer',
        senderId: collaborator,
        citations: [{ citationId: new Types.ObjectId() }],
        parts: [{ type: 'text', text: 'SECRET step' }],
        attachments: [{ recordId: 'r' }],
      } as unknown as IMessage
      const stub = stubCollaboratorTurn(turn, owner)
      expect(stub).to.deep.equal({ messageType: 'bot_response', content: FOREIGN_TURN_STUB, contentFormat: 'MARKDOWN', citations: [], foreignTurn: true })
      expect(JSON.stringify(stub)).to.not.include('SECRET')
    })
  })
})
