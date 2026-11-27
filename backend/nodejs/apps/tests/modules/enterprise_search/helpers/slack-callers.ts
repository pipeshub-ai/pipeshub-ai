import sinon from 'sinon'
import { setSlackUserLinkRepository } from '../../../../src/libs/repositories/slack-user-link'
import * as cmController from '../../../../src/modules/configuration_manager/controller/cm_controller'
import { SlackIdentityLinks } from '../../../../src/modules/configuration_manager/slack-identity/slack-identity-links'
import { setSlackIdentityLinksForTests } from '../../../../src/modules/configuration_manager/slack-identity/slack-linked-account'
import { InMemorySlackUserLinks } from '../../../support/slack/in-memory-slack-links'

/** The Slack workspace the bot serves in these suites. */
export const SLACK_TEAM = 'T0CHATROUTES'

/**
 * The Slack bot's scoped token for one workspace member, as the internal routes receive it: it names the bot's
 * configuration and the person's Slack team and user id. The account it answers as is the one that person linked.
 */
export const slackCallerRequest = (slackUserId: string, orgId: string): Record<string, unknown> => ({
  tokenPayload: { configId: 'cfg-1', slackTeamId: SLACK_TEAM, slackUserId, orgId },
})

/**
 * Links each Slack user id to a PipesHub account, as the person's own sign-in does. The bot serves no agent that
 * answers as its own service account, so a person with no link is refused. Undo with `unlinkSlackAccounts()`.
 */
export async function linkSlackAccounts(orgId: string, accounts: Readonly<Record<string, string>>): Promise<void> {
  const links = new InMemorySlackUserLinks()
  for (const [slackUserId, userId] of Object.entries(accounts)) {
    await links.upsert({
      orgId,
      workspaceId: SLACK_TEAM,
      teamId: SLACK_TEAM,
      slackUserId,
      userId,
      email: `${slackUserId.toLowerCase()}@slack.example`,
      slackName: slackUserId,
    })
  }
  setSlackUserLinkRepository(links)
  // The routes that hydrate without a key-value store read the shared links, which keep the repository they were made with.
  setSlackIdentityLinksForTests(new SlackIdentityLinks({ get: async () => null } as never, links))
  sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [] } as never)
}

export const unlinkSlackAccounts = (): void => {
  setSlackIdentityLinksForTests(undefined)
  setSlackUserLinkRepository()
}

/** What a stubbed `Users.findOne` hands back: a query that chains as the user repository chains it, resolving `row`. */
export const userRow = (row: unknown): Record<string, unknown> => {
  const chain: Record<string, unknown> = {
    select: () => chain,
    lean: () => chain,
    exec: () => Promise.resolve(row),
    then: (resolve: (found: unknown) => unknown, reject?: (error: unknown) => unknown) => Promise.resolve(row).then(resolve, reject),
  }
  return chain
}
