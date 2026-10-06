import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { INewChatSharing } from '../conversation-collaboration.service';
import { IAgentProfiles } from '../mentions/agent.directory';
import { IMentionTurnGate } from '../mentions/mention-turn-gate';
import { IRunLeaseManager } from '../leases/lease.types';
import { IConversationMessageFeed } from '../persistence/message-feed';

/** Collaborators the turn handlers share, resolved once at router build and passed to each handler factory. */
export interface ConversationTurnDeps {
  readonly feed: IConversationMessageFeed;
  readonly users: IUserDirectory;
  readonly leases: IRunLeaseManager;
  /** Admits a mentioned guest agent on a first send; absent, none is. */
  readonly mentions?: Pick<IMentionTurnGate, 'admitFirstSend'>;
  /** Names and handles of guest agents, for the history sent to the AI backend. */
  readonly agents?: IAgentProfiles;
  /** Shares a chat as it is created (first-send `share`); absent, a `share` is refused. */
  readonly sharing?: INewChatSharing;
}
