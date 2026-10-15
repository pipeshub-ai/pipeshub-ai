import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { IConversationHistory } from '../persistence/message-history';

/** Dependencies the turn handlers share, resolved once at router build and passed to each handler factory. */
export interface ConversationTurnDeps {
  readonly feed: IConversationHistory;
  readonly users: IUserDirectory;
}
