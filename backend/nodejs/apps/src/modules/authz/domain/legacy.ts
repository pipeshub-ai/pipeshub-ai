import {
  ConversationOperation,
  ConversationRef,
} from '../../enterprise_search/services/conversation/domain/types';
import { AccessPath, AccessPathType } from './types';

export type LegacyPathTypes = readonly Exclude<AccessPathType, 'team'>[];

const OWNER_ONLY: LegacyPathTypes = ['owner'];

const ops = (
  overrides: Partial<Record<ConversationOperation, LegacyPathTypes>>,
): Readonly<Record<ConversationOperation, LegacyPathTypes>> => ({
  read: OWNER_ONLY,
  feedback: OWNER_ONLY,
  leave: OWNER_ONLY,
  archiveSelf: OWNER_ONLY,
  send: OWNER_ONLY,
  cancel: OWNER_ONLY,
  regenerate: OWNER_ONLY,
  resume: OWNER_ONLY,
  invite: OWNER_ONLY,
  manageSharing: OWNER_ONLY,
  settings: OWNER_ONLY,
  transfer: OWNER_ONLY,
  rename: OWNER_ONLY,
  linkProject: OWNER_ONLY,
  delete: OWNER_ONLY,
  ...overrides,
});

/**
 * Who may do what under the default conversation policy: the access paths
 * that satisfy each operation. Write rows already collapse to read, team rows
 * are ignored, and every denial is a 404.
 */
export const LEGACY_REQUIREMENTS: Readonly<
  Record<
    ConversationRef['kind'],
    Readonly<Record<ConversationOperation, LegacyPathTypes>>
  >
> = {
  chat: ops({
    read: ['owner', 'direct', 'project'],
    feedback: ['owner', 'direct'],
  }),
  agent: ops({ read: ['owner', 'project'] }),
};

export function legacyAllows(
  kind: ConversationRef['kind'],
  op: ConversationOperation,
  via: readonly AccessPath[],
): boolean {
  const accepted = LEGACY_REQUIREMENTS[kind][op];
  return via.some((p) => (accepted as readonly string[]).includes(p.type));
}
