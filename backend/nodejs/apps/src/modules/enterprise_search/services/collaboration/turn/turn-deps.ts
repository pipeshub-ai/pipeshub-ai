import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { IRunLeaseManager } from '../leases/lease.types';
import { IConversationMessageFeed } from '../persistence/message-feed';

/** Collaborators the turn handlers share, resolved once at router build and passed to each handler factory. */
export interface ConversationTurnDeps {
  readonly feed: IConversationMessageFeed;
  readonly users: IUserDirectory;
  readonly leases: IRunLeaseManager;
}
