import type { ChatTitleReader } from '../../../../modules/agent-harness/ports/chat-titles';
import type { OrgMemberProfiles } from '../../../../modules/agent-harness/ports/org-members';
import { pickHarnessStore } from '../stores';
import { HarnessChatTitles, HarnessMemberProfiles } from './mongo';
import { PgChatTitles, PgMemberProfiles } from './pg';

export const createMemberProfiles = (): OrgMemberProfiles =>
  pickHarnessStore<OrgMemberProfiles>('agent-harness/audit-names', {
    mongo: () => new HarnessMemberProfiles(),
    postgres: () => new PgMemberProfiles(),
  });

export const createChatTitles = (): ChatTitleReader =>
  pickHarnessStore<ChatTitleReader>('agent-harness/audit-names', {
    mongo: () => new HarnessChatTitles(),
    postgres: () => new PgChatTitles(),
  });
