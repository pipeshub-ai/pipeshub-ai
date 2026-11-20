import { ConversationTurnDeps } from '../../../../src/modules/enterprise_search/services/conversation/turn/turn-deps'
import { MongoConversationHistory } from '../../../../src/modules/enterprise_search/services/conversation/persistence/message-history'

/** The handlers' shared dependencies over the in-memory store. */
export function turnDeps(over: Partial<ConversationTurnDeps> = {}): ConversationTurnDeps {
  return {
    feed: new MongoConversationHistory(),
    users: { displayNames: async () => new Map(), findByIds: async () => [] },
    ...over,
  }
}
