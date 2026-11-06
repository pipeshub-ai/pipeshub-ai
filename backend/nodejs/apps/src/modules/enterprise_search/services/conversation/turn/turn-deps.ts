import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { IConversationHistory } from '../persistence/message-history';
import { AppConfig } from '../../../../tokens_manager/config/config';

/** Dependencies the turn handlers share, resolved once at router build and passed to each handler factory. */
export interface ConversationTurnDeps {
  readonly feed: IConversationHistory;
  readonly users: IUserDirectory;
  /** Resolves the caller's teams for a project check; without it team membership grants no project access. */
  readonly appConfig?: AppConfig;
}
