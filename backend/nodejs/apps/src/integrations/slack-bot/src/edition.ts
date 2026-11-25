/**
 * The Slack bot's extension point (CONTRIBUTING.md, "Extension points"). The bot is one process with one entry, so an
 * edition plugs in by calling `installSlackBotEdition` in its own entry and then importing `./index`; nothing else of
 * the bot is copied. The default here serves the install's only organisation and keeps the thread map in the
 * configured document store.
 */
import {
  makeOrgContext,
  runWithOrgContext,
} from '../../../libs/context/orgContext';
import {
  setSlackThreadRepository,
  type SlackThreadRepository,
} from '../../../libs/repositories/slack-thread';
import { resolveSoleOrgId } from '../../../modules/slack_link/sole-org';
import type { SlackBotConfig } from './botRegistry';

export interface SlackBotEdition {
  /**
   * The organisation a bot configuration serves: whose links decide who a Slack person is, whose org the bot's token
   * names, and whose thread map a conversation is kept in. It throws when the bot serves none, and the bot then
   * answers nobody.
   */
  orgOfBot(bot: SlackBotConfig): Promise<string>;
  /**
   * The line under an answer a budget beyond the run's own stopped (`budgetScope` names it), in place of the
   * spending-limit line; null keeps that line.
   */
  budgetStopNotice(budgetScope: string, now: Date): string | null;
}

export interface SlackBotEditionInstall extends Partial<SlackBotEdition> {
  /** Replaces the thread map's repository. It reads the org from the org context `asBotOrg` binds. */
  threadRepository?: SlackThreadRepository;
}

export const defaultSlackBotEdition: SlackBotEdition = {
  orgOfBot: () => resolveSoleOrgId(),
  budgetStopNotice: () => null,
};

let installed: SlackBotEdition = defaultSlackBotEdition;

/** Call before `./index` is imported; a later call replaces what an earlier one installed. */
export function installSlackBotEdition(edition: SlackBotEditionInstall): void {
  installed = {
    ...defaultSlackBotEdition,
    ...(edition.orgOfBot ? { orgOfBot: edition.orgOfBot } : {}),
    ...(edition.budgetStopNotice
      ? { budgetStopNotice: edition.budgetStopNotice }
      : {}),
  };
  if (edition.threadRepository)
    setSlackThreadRepository(edition.threadRepository);
}

export function slackBotEdition(): SlackBotEdition {
  return installed;
}

/** Back to the default (tests). */
export function resetSlackBotEditionForTests(): void {
  installed = defaultSlackBotEdition;
  setSlackThreadRepository();
}

/** Runs `fn` as the organisation the bot serves, so the thread map it reads or writes is that organisation's. */
export function asBotOrg<T>(orgId: string, fn: () => Promise<T>): Promise<T> {
  return runWithOrgContext(
    makeOrgContext({
      orgId,
      userId: null,
      kind: 'service',
      source: 'slack-bot',
    }),
    fn,
  );
}
