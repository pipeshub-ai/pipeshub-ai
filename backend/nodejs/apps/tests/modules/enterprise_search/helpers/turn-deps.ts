import { ConversationTurnDeps } from '../../../../src/modules/enterprise_search/services/collaboration/turn/turn-deps'
import { MongoConversationMessageFeed } from '../../../../src/modules/enterprise_search/services/collaboration/persistence/message-feed'

/** The handlers' shared collaborators over the in-memory store. The default lease manager refuses: a follow-up's guard acquires its lease, and a first send needs `turnWorld`'s. */
export function turnDeps(over: Partial<ConversationTurnDeps> = {}): ConversationTurnDeps {
  return {
    feed: new MongoConversationMessageFeed(),
    users: { displayNames: async () => new Map(), findByIds: async () => [] },
    leases: {
      acquire: () => Promise.reject(new Error('the guard acquires the lease')),
      forNewSession: () => {
        throw new Error('this test did not configure a lease manager for first sends')
      },
    },
    ...over,
  }
}
