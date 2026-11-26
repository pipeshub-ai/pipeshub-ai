import { SlackUserLinkModel } from './schema/slack-user-link.schema';
import type {
  NewSlackUserLink,
  SlackUserLink,
  SlackUserLinkRepository,
} from './types';

const plain = (row: Record<string, unknown>): SlackUserLink => ({
  orgId: String(row.orgId),
  workspaceId: String(row.workspaceId),
  slackUserId: String(row.slackUserId),
  teamId: String(row.teamId),
  userId: String(row.userId),
  email: String(row.email),
  slackName: typeof row.slackName === 'string' ? row.slackName : '',
  linkedAt: new Date(row.linkedAt as Date),
});

const keyOf = (
  orgId: string,
  workspaceId: string,
  slackUserId: string,
): { orgId: string; workspaceId: string; slackUserId: string } => ({
  orgId,
  workspaceId,
  slackUserId,
});

/** The first index keyed the account without its org; the org's own index replaces it (it held no released rows). */
const PRE_ORG_INDEX = 'workspaceId_1_slackUserId_1';

export async function dropPreOrgSlackUserLinkIndex(): Promise<void> {
  try {
    const indexes = await SlackUserLinkModel.collection.indexes();
    if (indexes.some((i) => i.name === PRE_ORG_INDEX)) {
      await SlackUserLinkModel.collection.dropIndex(PRE_ORG_INDEX);
    }
    await SlackUserLinkModel.createIndexes();
  } catch (error) {
    console.warn(
      'Slack account link indexes not checked:',
      error instanceof Error ? error.message : String(error),
    );
  }
}

export class MongoSlackUserLinkRepository implements SlackUserLinkRepository {
  async get(
    orgId: string,
    workspaceId: string,
    slackUserId: string,
  ): Promise<SlackUserLink | null> {
    const row = await SlackUserLinkModel.findOne(
      keyOf(orgId, workspaceId, slackUserId),
    ).lean();
    return row ? plain(row as unknown as Record<string, unknown>) : null;
  }

  async upsert(link: NewSlackUserLink): Promise<SlackUserLink> {
    const linkedAt = new Date();
    await SlackUserLinkModel.updateOne(
      keyOf(link.orgId, link.workspaceId, link.slackUserId),
      { $set: { ...link, linkedAt } },
      { upsert: true },
    );
    return { ...link, linkedAt };
  }

  async remove(
    orgId: string,
    workspaceId: string,
    slackUserId: string,
  ): Promise<void> {
    await SlackUserLinkModel.deleteOne(keyOf(orgId, workspaceId, slackUserId));
  }

  async listForOrg(orgId: string): Promise<SlackUserLink[]> {
    const rows = await SlackUserLinkModel.find({ orgId })
      .sort({ linkedAt: 1 })
      .lean();
    return rows.map((row) => plain(row as unknown as Record<string, unknown>));
  }

  async removeForUser(orgId: string, userId: string): Promise<number> {
    const { deletedCount } = await SlackUserLinkModel.deleteMany({
      orgId,
      userId,
    });
    return deletedCount;
  }

  async removeForOrg(orgId: string): Promise<number> {
    const { deletedCount } = await SlackUserLinkModel.deleteMany({ orgId });
    return deletedCount;
  }
}
