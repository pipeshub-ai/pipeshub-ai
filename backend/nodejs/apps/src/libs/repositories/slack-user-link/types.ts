/**
 * Slack account links (ADR-SLACKID-01): the PipesHub account a person confirmed, signed in, as the owner of a Slack
 * account. The Slack bot acts as a person only through one of these, never through a Slack profile email. A link is
 * an organisation's own: one Slack workspace can hold bots of several organisations, and each keeps its own links.
 */
export interface SlackUserLink {
  orgId: string;
  /** The Slack identity space: the Enterprise Grid id when the workspace has one, else the workspace (team) id. */
  workspaceId: string;
  slackUserId: string;
  /** The workspace the account was in when linked. */
  teamId: string;
  userId: string;
  /** The PipesHub email when linked: a label and the bot's thread key, never an identity. */
  email: string;
  /** The Slack name shown on the link page, so a person can recognise the link later. */
  slackName: string;
  linkedAt: Date;
}

export type NewSlackUserLink = Omit<SlackUserLink, 'linkedAt'>;

export interface SlackUserLinkRepository {
  /** The organisation's link of one Slack account; null when it has none. */
  get(
    orgId: string,
    workspaceId: string,
    slackUserId: string,
  ): Promise<SlackUserLink | null>;
  /** Creates the link, or moves the Slack account to this person within the same organisation. */
  upsert(link: NewSlackUserLink): Promise<SlackUserLink>;
  remove(
    orgId: string,
    workspaceId: string,
    slackUserId: string,
  ): Promise<void>;
  /** Every link of the organisation (the admins' list), oldest first. */
  listForOrg(orgId: string): Promise<SlackUserLink[]>;
  /** Every link of one person (a deleted account's erasure); how many were removed. */
  removeForUser(orgId: string, userId: string): Promise<number>;
  /** Every link of the organisation (its deletion); how many were removed. */
  removeForOrg(orgId: string): Promise<number>;
}
