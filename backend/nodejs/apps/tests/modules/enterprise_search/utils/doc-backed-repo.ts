import { MongoConversationRepository } from '../../../../src/libs/repositories/conversation/mongo'
import { setConversationRepository } from '../../../../src/libs/repositories/conversation'
import { sessionOf } from '../../../../src/libs/repositories/mongo'

/**
 * Applies a `recordRunState` change to a document-like conversation and "saves" it through
 * that object's own `save` stub, the way the persistence helpers used to.
 */
async function applyToDoc(conversation: any, change: any, tx?: any): Promise<any> {
  if (change.status !== undefined) conversation.status = change.status
  if (change.failReason === null) conversation.failReason = undefined
  else if (change.failReason !== undefined) conversation.failReason = change.failReason
  if (change.lastActivityAt !== undefined) conversation.lastActivityAt = change.lastActivityAt
  if (change.modelInfo) {
    for (const [k, v] of Object.entries(change.modelInfo)) {
      if (v !== undefined && v !== null) (conversation.modelInfo ??= {})[k] = v
    }
  }
  if (change.appendError) {
    if (!conversation.conversationErrors) conversation.conversationErrors = []
    conversation.conversationErrors.push({ ...change.appendError, timestamp: new Date() })
  }
  const session = sessionOf(tx)
  const saved = await (session ? conversation.save({ session }) : conversation.save())
  if (!saved) return null
  return typeof saved.toObject === 'function' ? saved.toObject() : saved
}

/**
 * A conversation repository whose `recordRunState` writes through one document-like object.
 * Everything else is the real Mongo repository (so the tests' model stubs still see the
 * message and citation queries). Returns the function that restores the default.
 */
export function useDocumentAsSession(conversation: any): () => void {
  const repo = new MongoConversationRepository()
  repo.recordRunState = (_id: string, change: any, tx?: any) => applyToDoc(conversation, change, tx)
  setConversationRepository(repo)
  return () => setConversationRepository()
}

const registry = new Map<string, any>()

/** Registers a document-like conversation so `recordRunState` for its id writes through it. */
export function registerSessionDoc<T extends { _id?: unknown }>(doc: T): T {
  registry.set(String(doc._id), doc)
  return doc
}

/** Routes `recordRunState` to the registered document of that id (the real repository otherwise). */
export function installSessionDocRegistry(): () => void {
  const repo = new MongoConversationRepository()
  const real = repo.recordRunState.bind(repo)
  repo.recordRunState = (id: string, change: any, tx?: any) => {
    const doc = registry.get(id)
    return doc ? applyToDoc(doc, change, tx) : real(id, change, tx)
  }
  setConversationRepository(repo)
  return () => {
    registry.clear()
    setConversationRepository()
  }
}
