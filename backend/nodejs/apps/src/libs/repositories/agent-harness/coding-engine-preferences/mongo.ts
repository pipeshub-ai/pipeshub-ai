import { Org } from '../../../../modules/user_management/schema/org.schema';
import { Users } from '../../../../modules/user_management/schema/users.schema';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { HarnessNotFoundError } from '../../../../modules/agent-harness/domain/errors';
import type {
  CodingEnginePreferences,
  StoredCodingEngines,
} from '../../../../modules/agent-harness/ports/coding-engines';
import type { SessionRepository } from '../../../../modules/agent-harness/domain/repository';
import type { PersonalMemoryChoice } from '../../../../modules/agent-harness/ports/memory-store';

const stringOrNull = (value: unknown): string | null =>
  typeof value === 'string' && value.length > 0 ? value : null;

/**
 * The coding-engine choices on the user and org documents (ENGPICK), and the person's own memory switch on theirs;
 * the user is always found within its org.
 */
export class MongoCodingEnginePreferences
  implements CodingEnginePreferences, PersonalMemoryChoice
{
  async memoryOff(orgId: string, userId: string): Promise<boolean> {
    const user = await Users.findOne(
      { _id: userId, orgId, isDeleted: { $ne: true } },
      { harnessMemoryOff: 1 },
    )
      .lean()
      .exec();
    return user?.harnessMemoryOff === true;
  }

  /** Stored only when off, so a person who never chose reads the default (on). */
  async writeMemoryOff(
    orgId: string,
    userId: string,
    off: boolean,
  ): Promise<void> {
    const result = await Users.updateOne(
      { _id: userId, orgId, isDeleted: { $ne: true } },
      off
        ? { $set: { harnessMemoryOff: true } }
        : { $unset: { harnessMemoryOff: 1 } },
    ).exec();
    if (result.matchedCount === 0)
      throw new HarnessNotFoundError('User not found');
  }

  async read(orgId: string, userId: string): Promise<StoredCodingEngines> {
    const [user, org] = await Promise.all([
      Users.findOne(
        { _id: userId, orgId, isDeleted: { $ne: true } },
        { codingEngine: 1 },
      )
        .lean()
        .exec(),
      Org.findOne(
        { _id: orgId, isDeleted: { $ne: true } },
        { defaultCodingEngine: 1, codingEngineAutoRoute: 1 },
      )
        .lean()
        .exec(),
    ]);
    return {
      user: stringOrNull(user?.codingEngine),
      org: stringOrNull(org?.defaultCodingEngine),
      orgAutoRoute: org?.codingEngineAutoRoute !== false,
    };
  }

  /** Stored only when off, so an org that never chose reads the default (on). */
  async writeOrgAutoRoute(orgId: string, on: boolean): Promise<void> {
    const result = await Org.updateOne(
      { _id: orgId, isDeleted: { $ne: true } },
      on
        ? { $unset: { codingEngineAutoRoute: 1 } }
        : { $set: { codingEngineAutoRoute: false } },
    ).exec();
    if (result.matchedCount === 0)
      throw new HarnessNotFoundError('Organization not found');
  }

  async writeUser(
    orgId: string,
    userId: string,
    choice: string | null,
  ): Promise<void> {
    const result = await Users.updateOne(
      { _id: userId, orgId, isDeleted: { $ne: true } },
      choice === null
        ? { $unset: { codingEngine: 1 } }
        : { $set: { codingEngine: choice } },
    ).exec();
    if (result.matchedCount === 0)
      throw new HarnessNotFoundError('User not found');
  }

  async writeOrg(orgId: string, engine: string | null): Promise<void> {
    const result = await Org.updateOne(
      { _id: orgId, isDeleted: { $ne: true } },
      engine === null
        ? { $unset: { defaultCodingEngine: 1 } }
        : { $set: { defaultCodingEngine: engine } },
    ).exec();
    if (result.matchedCount === 0)
      throw new HarnessNotFoundError('Organization not found');
  }

  async readSession(orgId: string, sessionId: string): Promise<string | null> {
    const session = await ChatSession.findOne(
      { _id: sessionId, orgId, isDeleted: { $ne: true } },
      { codingEngine: 1 },
    )
      .lean()
      .exec();
    return stringOrNull(session?.codingEngine);
  }

  async writeSession(
    orgId: string,
    sessionId: string,
    choice: string | null,
  ): Promise<void> {
    const result = await ChatSession.updateOne(
      { _id: sessionId, orgId, isDeleted: { $ne: true } },
      choice === null
        ? { $unset: { codingEngine: 1 } }
        : { $set: { codingEngine: choice } },
    ).exec();
    if (result.matchedCount === 0)
      throw new HarnessNotFoundError('Conversation not found');
  }

  async readSessionPin(
    orgId: string,
    sessionId: string,
  ): Promise<string | null> {
    const session = await ChatSession.findOne(
      { _id: sessionId, orgId, isDeleted: { $ne: true } },
      { codingEnginePin: 1 },
    )
      .lean()
      .exec();
    return stringOrNull(session?.codingEnginePin);
  }

  async writeSessionPin(
    orgId: string,
    sessionId: string,
    engine: string | null,
  ): Promise<void> {
    const result = await ChatSession.updateOne(
      { _id: sessionId, orgId, isDeleted: { $ne: true } },
      engine === null
        ? { $unset: { codingEnginePin: 1 } }
        : { $set: { codingEnginePin: engine } },
    ).exec();
    if (result.matchedCount === 0)
      throw new HarnessNotFoundError('Conversation not found');
  }

  async readSessionRepository(
    orgId: string,
    sessionId: string,
  ): Promise<SessionRepository | null> {
    const session = await ChatSession.findOne(
      { _id: sessionId, orgId, isDeleted: { $ne: true } },
      { repository: 1 },
    )
      .lean()
      .exec();
    const stored = session?.repository;
    if (
      typeof stored?.url !== 'string' ||
      typeof stored.toolsetInstanceId !== 'string'
    ) {
      return null;
    }
    return {
      url: stored.url,
      toolsetInstanceId: stored.toolsetInstanceId,
      ...(typeof stored.baseRef === 'string'
        ? { baseRef: stored.baseRef }
        : {}),
    };
  }

  async writeSessionRepository(
    orgId: string,
    sessionId: string,
    repository: SessionRepository | null,
  ): Promise<void> {
    const result = await ChatSession.updateOne(
      { _id: sessionId, orgId, isDeleted: { $ne: true } },
      repository === null
        ? { $unset: { repository: 1 } }
        : {
            $set: {
              repository: {
                url: repository.url,
                toolsetInstanceId: repository.toolsetInstanceId,
                ...(repository.baseRef !== undefined
                  ? { baseRef: repository.baseRef }
                  : {}),
              },
            },
          },
    ).exec();
    if (result.matchedCount === 0)
      throw new HarnessNotFoundError('Conversation not found');
  }

  /** Whether or not the person is still live: their erasure runs after the soft delete. */
  async eraseUser(orgId: string, userId: string): Promise<number> {
    const result = await Users.updateOne(
      {
        _id: userId,
        orgId,
        $or: [
          { codingEngine: { $exists: true } },
          { harnessMemoryOff: { $exists: true } },
        ],
      },
      { $unset: { codingEngine: 1, harnessMemoryOff: 1 } },
    ).exec();
    return result.modifiedCount;
  }

  /** Whether or not the conversation is still live: its erasure runs after the soft delete. */
  async eraseSession(orgId: string, sessionId: string): Promise<number> {
    const result = await ChatSession.updateOne(
      {
        _id: sessionId,
        orgId,
        $or: [
          { codingEngine: { $exists: true } },
          { codingEnginePin: { $exists: true } },
          { repository: { $exists: true } },
        ],
      },
      { $unset: { codingEngine: 1, codingEnginePin: 1, repository: 1 } },
    ).exec();
    return result.modifiedCount;
  }
}
