import type {
  NewSlackUserLink,
  SlackUserLink,
  SlackUserLinkRepository,
} from '../../../src/libs/repositories/slack-user-link/types'

/** The Slack account links in memory, with the store's contract: one link per Slack account and organisation. */
export class InMemorySlackUserLinks implements SlackUserLinkRepository {
  readonly rows = new Map<string, SlackUserLink>()
  readonly removed: string[] = []

  private key(orgId: string, workspaceId: string, slackUserId: string): string {
    return `${orgId}\u0000${workspaceId}\u0000${slackUserId}`
  }

  async get(orgId: string, workspaceId: string, slackUserId: string): Promise<SlackUserLink | null> {
    return this.rows.get(this.key(orgId, workspaceId, slackUserId)) ?? null
  }

  async upsert(link: NewSlackUserLink): Promise<SlackUserLink> {
    const row = { ...link, linkedAt: new Date() }
    this.rows.set(this.key(link.orgId, link.workspaceId, link.slackUserId), row)
    return row
  }

  async remove(orgId: string, workspaceId: string, slackUserId: string): Promise<void> {
    this.removed.push(this.key(orgId, workspaceId, slackUserId))
    this.rows.delete(this.key(orgId, workspaceId, slackUserId))
  }

  async listForOrg(orgId: string): Promise<SlackUserLink[]> {
    return [...this.rows.values()].filter((r) => r.orgId === orgId).sort((a, b) => a.linkedAt.getTime() - b.linkedAt.getTime())
  }

  async removeForUser(orgId: string, userId: string): Promise<number> {
    let n = 0
    for (const [k, row] of this.rows) {
      if (row.orgId === orgId && row.userId === userId) {
        this.rows.delete(k)
        n += 1
      }
    }
    return n
  }

  async removeForOrg(orgId: string): Promise<number> {
    let n = 0
    for (const [k, row] of this.rows) {
      if (row.orgId === orgId) {
        this.rows.delete(k)
        n += 1
      }
    }
    return n
  }
}
