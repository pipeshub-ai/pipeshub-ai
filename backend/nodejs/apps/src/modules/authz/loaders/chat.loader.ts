import { Types } from 'mongoose';
import {
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../../enterprise_search/constants/constants';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import {
  ConversationAccessFields,
  ConversationRef,
} from '../../enterprise_search/services/collaboration/domain/types';
import { Project } from '../../projects/schema/project.schema';
import { ProjectFacts } from '../domain/types';
import {
  IChatAccessLoader,
  IScopedChatLoader,
  LoadedChat,
  ScopedLoadedChat,
  ScopedSession,
} from '../ports';
import { projectFactsOf } from './project.loader';

const SESSION_FIELDS =
  'orgId userId isDeleted sharedWith projectId projectVisibility settings aclVersion';

const SCOPED_FIELDS = `${SESSION_FIELDS} initiator isArchived agentKey`;

export class ChatAccessLoader implements IChatAccessLoader, IScopedChatLoader {
  async load(orgId: string, chatId: string): Promise<LoadedChat | null> {
    if (!Types.ObjectId.isValid(chatId) || !Types.ObjectId.isValid(orgId)) {
      return null;
    }
    const session = await ChatSession.findOne({ _id: chatId, orgId })
      .select(SESSION_FIELDS)
      .lean<ConversationAccessFields>();
    if (!session) {
      return null;
    }
    return { session, project: await this.projectFacts(session) };
  }

  async loadScoped(
    orgId: string,
    target: { id: string; kind: ConversationRef['kind']; agentKey?: string },
  ): Promise<ScopedLoadedChat | null> {
    if (!Types.ObjectId.isValid(target.id) || !Types.ObjectId.isValid(orgId)) {
      return null;
    }
    if (target.kind === 'agent' && (target.agentKey ?? '') === '') {
      return null;
    }
    const kindFilter =
      target.kind === 'chat'
        ? EXCLUDE_AGENT
        : { ...ONLY_AGENT, agentKey: target.agentKey };
    const session = await ChatSession.findOne({
      _id: target.id,
      orgId,
      isDeleted: false,
      ...kindFilter,
    })
      .select(SCOPED_FIELDS)
      .lean<ScopedSession>();
    if (!session) {
      return null;
    }
    return { session, project: await this.projectFacts(session) };
  }

  private async projectFacts(
    session: ConversationAccessFields,
  ): Promise<ProjectFacts | null> {
    if (!session.projectId) {
      return null;
    }
    const project = await Project.findOne({
      _id: session.projectId.toString(),
      orgId: session.orgId.toString(),
      isDeleted: false,
    }).lean();
    return project ? projectFactsOf(project) : null;
  }
}
