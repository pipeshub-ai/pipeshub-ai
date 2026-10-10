import { CanonicalRole, maxRole } from './ladder';
import { collectChatPaths } from './rules';
import { AccessPath, ChatFacts, Subject } from './types';

export interface ChatExplanation {
  role: CanonicalRole;
  via: readonly AccessPath[];
  /** True when team rows exist but the subject's teams could not be resolved, so team paths may be missing. */
  teamsUnresolved: boolean;
}

export function explainChat(
  chat: ChatFacts,
  subject: Subject,
  options: { collab: boolean },
): ChatExplanation {
  const { via, teamsUnresolved } = collectChatPaths(
    chat,
    subject,
    options.collab,
  );
  return { role: maxRole(...via.map((p) => p.role)), via, teamsUnresolved };
}
