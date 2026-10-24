/**
 * What the model is shown after a fork, against a real MongoDB. Skipped unless
 * FORK_TEST_MONGO_URI is set; run it once with a replica set and
 * REPLICA_SET_AVAILABLE=true, and once against a standalone server.
 *
 * Every follow-up question goes through `continueConversation`, the code that
 * builds the history the AI backend receives, and every answer is stored with
 * `saveCompleteConversation`. Only the model is replaced: `fakeModel` can answer
 * only from the history it is handed, so a leaked or a missing message shows up
 * as a wrong answer.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { copyArtifactsForFork, copySessionUpToMessage } from '../../../../src/modules/enterprise_search/utils/fork-conversation'
import type { ArtifactCopier, ArtifactRef } from '../../../../src/modules/enterprise_search/utils/fork-artifacts'
import { continueConversation } from '../../../../src/modules/enterprise_search/utils/non-streaming-chat'
import { saveCompleteConversation } from '../../../../src/modules/enterprise_search/utils/utils'
import type { IAIResponse, IChatSessionDocument } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'

const URI = process.env.FORK_TEST_MONGO_URI
const useTransactions = process.env.REPLICA_SET_AVAILABLE === 'true'
const oid = () => new mongoose.Types.ObjectId()
const describeIfMongo = URI ? describe : describe.skip

type HistoryRow = { role: string; content: string }

/** Answers only from what the history says, the way a model can only use what it is shown. */
const fakeModel = (history: HistoryRow[], question: string): string => {
  const said = history.filter((m) => m.role === 'user_query').map((m) => m.content)
  const latest = (re: RegExp): string | undefined => {
    for (let i = said.length - 1; i >= 0; i--) {
      const hit = re.exec(said[i]!)
      if (hit) return hit[1]
    }
    return undefined
  }
  if (/what is my name/i.test(question)) {
    const name = latest(/my name is (\w+)/i)
    return name ? `Your name is ${name}` : 'I do not know your name'
  }
  if (/what is the budget/i.test(question)) {
    const budget = latest(/budget is (\w+)/i)
    return budget ? `The budget is ${budget}` : 'I do not know the budget'
  }
  return `Noted: ${question}`
}

describeIfMongo(`history after a fork on a real MongoDB (transactions: ${useTransactions})`, function () {
  this.timeout(60000)
  const orgId = oid()
  const owner = oid()

  before(async () => {
    await mongoose.connect(URI as string, { dbName: `fork_history_test_${Date.now()}` })
    await ChatSession.syncIndexes()
    await ChatSessionMessage.syncIndexes()
  })

  after(async () => {
    await mongoose.connection.dropDatabase()
    await mongoose.disconnect()
  })

  const newChat = async (): Promise<IChatSessionDocument> =>
    (await ChatSession.create({
      sessionType: 'chat',
      userId: owner,
      initiator: owner,
      orgId,
      title: 'Planning',
    })) as unknown as IChatSessionDocument

  /** One real turn: history is built the way the controller builds it, then the answer is stored. */
  const ask = async (chat: IChatSessionDocument, question: string, answer?: string) => {
    const now = new Date()
    const { conversation, previousConversations } = await continueConversation(
      { _id: chat._id, orgId, userId: owner },
      { messageType: 'user_query', content: question, contentFormat: 'MARKDOWN', createdAt: now, updatedAt: now },
      // Every turn here is the owner's own, so no access check is made.
      { appConfig: {} as never, userId: String(owner), orgId: String(orgId) },
    )
    const history = previousConversations as unknown as HistoryRow[]
    const reply = answer ?? fakeModel(history, question)
    await saveCompleteConversation(
      conversation,
      { answer: reply, citations: [], followUpQuestions: [] } as unknown as IAIResponse,
      String(orgId),
    )
    return { history, reply }
  }

  const transcript = async (chat: IChatSessionDocument): Promise<string[]> =>
    (await ChatSessionMessage.find({ sessionId: chat._id }).sort({ seq: 1 }).lean()).map((m) => m.content)

  const messageIds = async (chat: IChatSessionDocument): Promise<string[]> =>
    (await ChatSessionMessage.find({ sessionId: chat._id }).sort({ seq: 1 }).lean()).map((m) => String(m._id))

  const forkAt = async (
    chat: IChatSessionDocument,
    position: number,
    copyArtifacts?: ArtifactCopier,
  ): Promise<IChatSessionDocument> => {
    const rows = await ChatSessionMessage.find({ sessionId: chat._id }).sort({ seq: 1 }).lean()
    const point = String(rows[position - 1]!._id)
    const source = (await ChatSession.findById(chat._id)) as IChatSessionDocument
    // Copied first, outside the transaction, the way the controller does it.
    const artifacts = copyArtifacts
      ? await copyArtifactsForFork(source, point, new mongoose.Types.ObjectId(), copyArtifacts)
      : undefined
    const run = (mongoSession: mongoose.ClientSession | null) =>
      copySessionUpToMessage(
        source,
        point,
        String(owner),
        String(orgId),
        { sessionType: 'chat' },
        mongoSession,
        artifacts,
      )
    if (!useTransactions) return (await run(null)).conversation
    const session = await mongoose.startSession()
    try {
      const result = await session.withTransaction(() => run(session))
      return (result as Awaited<ReturnType<typeof run>>).conversation
    } finally {
      await session.endSession()
    }
  }

  /** Three turns: name Asha, budget 4M, then both change. Messages 1-4 are turns one and two. */
  const seededChat = async (): Promise<IChatSessionDocument> => {
    const chat = await newChat()
    await ask(chat, 'My name is Asha', 'Nice to meet you, Asha')
    await ask(chat, 'Our budget is 4M', 'Noted, the budget is 4M')
    await ask(chat, 'Actually my name is Ravi and the budget is 9M', 'Updated: Ravi and 9M')
    return chat
  }

  const FIRST_FOUR = ['My name is Asha', 'Nice to meet you, Asha', 'Our budget is 4M', 'Noted, the budget is 4M']

  it('answers a question about the earlier messages from the fork, with exactly the earlier history', async () => {
    const chat = await seededChat()
    const fork = await forkAt(chat, 4)

    const name = await ask(fork, 'What is my name?')
    expect(name.history.map((m) => m.content)).to.deep.equal(FIRST_FOUR)
    expect(name.history.map((m) => m.role)).to.deep.equal(['user_query', 'bot_response', 'user_query', 'bot_response'])
    expect(name.reply).to.equal('Your name is Asha')

    const budget = await ask(fork, 'What is the budget?')
    expect(budget.reply).to.equal('The budget is 4M')
  })

  it('never shows the fork the messages that came after the fork point', async () => {
    const chat = await seededChat()
    const fork = await forkAt(chat, 4)

    const { history } = await ask(fork, 'What is my name?')
    const joined = history.map((m) => m.content).join('\n')
    expect(joined).to.not.contain('Ravi')
    expect(joined).to.not.contain('9M')
    expect(history).to.have.length(4)
  })

  it('finds the artifact cards in the copied messages and points them at the fork\'s own copy', async () => {
    const card = (version: number) => `::artifact[r.md](record:old){text/markdown|doc-old|old|DOCUMENT|${version}}`
    const chat = await newChat()
    await ask(chat, 'Write the report', `Here\n\n${card(1)}`)
    await ask(chat, 'Edit it', `Edited\n\n${card(2)}`)
    await ask(chat, 'No artifact here', 'Sure')
    const asked: { conversationId: string; refs: ArtifactRef[] }[] = []
    const copyArtifacts: ArtifactCopier = async (conversationId, refs) => {
      asked.push({ conversationId, refs })
      return {
        copies: [
          {
            sourceArtifactId: 'old',
            sourceDocumentId: 'doc-old',
            artifactId: 'new',
            documentId: 'doc-new',
            versions: [{ from: 1, to: 1 }],
          },
        ],
        failed: [],
      }
    }

    const fork = await forkAt(chat, 2, copyArtifacts)

    expect(asked).to.deep.equal([{ conversationId: String(fork._id), refs: [{ artifactId: 'old', upToVersion: 1 }] }])
    expect(await transcript(fork)).to.deep.equal([
      'Write the report',
      'Here\n\n::artifact[r.md](record:new){text/markdown|doc-new|new|DOCUMENT|1}',
    ])
    expect((await transcript(chat))[3]).to.equal(`Edited\n\n${card(2)}`)
  })

  it('keeps the original answering from its own, longer history after the fork was used', async () => {
    const chat = await seededChat()
    const fork = await forkAt(chat, 4)
    await ask(fork, 'My name is Meera', 'Hello Meera')
    await ask(fork, 'What is my name?')

    const name = await ask(chat, 'What is my name?')
    expect(name.reply).to.equal('Your name is Ravi')
    const budget = await ask(chat, 'What is the budget?')
    expect(budget.reply).to.equal('The budget is 9M')
    const joined = [...name.history, ...budget.history].map((m) => m.content).join('\n')
    expect(joined).to.not.contain('Meera')
    expect(name.history).to.have.length(6)
  })

  it('does not carry what happens in the original after the fork into the fork', async () => {
    const chat = await seededChat()
    const fork = await forkAt(chat, 4)

    await ask(chat, 'My name is Zoya', 'Hello Zoya')
    await ask(chat, 'Our budget is 100M', 'Noted, 100M')

    const name = await ask(fork, 'What is my name?')
    expect(name.reply).to.equal('Your name is Asha')
    expect(name.history.map((m) => m.content)).to.deep.equal(FIRST_FOUR)
    const budget = await ask(fork, 'What is the budget?')
    expect(budget.reply).to.equal('The budget is 4M')
  })

  it('keeps two chats apart while both keep going, turn by turn', async () => {
    const chat = await seededChat()
    const fork = await forkAt(chat, 4)

    await ask(chat, 'My name is Zoya', 'Hello Zoya')
    await ask(fork, 'My name is Meera', 'Hello Meera')
    await ask(chat, 'Our budget is 100M', 'Noted, 100M')
    await ask(fork, 'Our budget is 1M', 'Noted, 1M')

    expect((await ask(chat, 'What is my name?')).reply).to.equal('Your name is Zoya')
    expect((await ask(fork, 'What is my name?')).reply).to.equal('Your name is Meera')
    expect((await ask(chat, 'What is the budget?')).reply).to.equal('The budget is 100M')
    expect((await ask(fork, 'What is the budget?')).reply).to.equal('The budget is 1M')

    const original = await transcript(chat)
    const forked = await transcript(fork)
    expect(original.some((c) => c.includes('Meera') || c.includes('1M'))).to.equal(false)
    expect(forked.some((c) => c.includes('Zoya') || c.includes('100M') || c.includes('Ravi'))).to.equal(false)
    expect(forked.slice(0, 4)).to.deep.equal(FIRST_FOUR)
  })

  it('gives a fork from the last message the whole history', async () => {
    const chat = await seededChat()
    const fork = await forkAt(chat, 6)

    const { history, reply } = await ask(fork, 'What is my name?')
    expect(history).to.have.length(6)
    expect(reply).to.equal('Your name is Ravi')
  })

  it('gives a fork from an early message only what came before it', async () => {
    const chat = await seededChat()
    const fork = await forkAt(chat, 2)

    const name = await ask(fork, 'What is my name?')
    expect(name.history.map((m) => m.content)).to.deep.equal(FIRST_FOUR.slice(0, 2))
    expect(name.reply).to.equal('Your name is Asha')
    expect((await ask(fork, 'What is the budget?')).reply).to.equal('I do not know the budget')
  })

  it('keeps two forks of the same message independent of each other', async () => {
    const chat = await seededChat()
    const a = await forkAt(chat, 4)
    const b = await forkAt(chat, 4)

    await ask(a, 'My name is Meera', 'Hello Meera')
    expect((await ask(b, 'What is my name?')).reply).to.equal('Your name is Asha')
    expect((await ask(a, 'What is my name?')).reply).to.equal('Your name is Meera')
    expect(await transcript(b)).to.have.length(6)
  })

  it('lets a fork of a fork see its parent fork, and nothing the root chat did later', async () => {
    const chat = await seededChat()
    const first = await forkAt(chat, 4)
    await ask(first, 'My name is Meera', 'Hello Meera')
    await ask(chat, 'My name is Zoya', 'Hello Zoya')

    const second = await forkAt(first, 6)
    const { history, reply } = await ask(second, 'What is my name?')
    expect(reply).to.equal('Your name is Meera')
    expect(history.map((m) => m.content)).to.deep.equal([...FIRST_FOUR, 'My name is Meera', 'Hello Meera'])
  })

  it('numbers the fork contiguously as it grows and leaves the original untouched', async () => {
    const chat = await seededChat()
    const beforeIds = await messageIds(chat)
    const beforeText = await transcript(chat)
    const beforeSeq = (await ChatSession.findById(chat._id).select('+nextSeq').lean())!.nextSeq

    const fork = await forkAt(chat, 4)
    await ask(fork, 'My name is Meera', 'Hello Meera')
    await ask(fork, 'What is my name?')

    const forkRows = await ChatSessionMessage.find({ sessionId: fork._id }).sort({ seq: 1 }).lean()
    expect(forkRows.map((m) => m.seq)).to.deep.equal([1, 2, 3, 4, 5, 6, 7, 8])
    expect(forkRows.every((m) => String(m.sessionId) === String(fork._id))).to.equal(true)

    expect(await messageIds(chat)).to.deep.equal(beforeIds)
    expect(await transcript(chat)).to.deep.equal(beforeText)
    expect((await ChatSession.findById(chat._id).select('+nextSeq').lean())!.nextSeq).to.equal(beforeSeq)
    const forkIds = new Set(await messageIds(fork))
    expect(beforeIds.some((id) => forkIds.has(id))).to.equal(false)
  })

  it('remembers where the fork came from', async () => {
    const chat = await seededChat()
    const rows = await ChatSessionMessage.find({ sessionId: chat._id }).sort({ seq: 1 }).lean()
    const fork = await forkAt(chat, 4)
    const stored = await ChatSession.findById(fork._id).lean()
    const meta = stored!.metadata as unknown as Map<string, string> | Record<string, string>
    const get = (k: string) => (meta instanceof Map ? meta.get(k) : meta[k])
    expect(get('forkedFromConversationId')).to.equal(String(chat._id))
    expect(get('forkedFromMessageId')).to.equal(String(rows[3]!._id))
  })
})
