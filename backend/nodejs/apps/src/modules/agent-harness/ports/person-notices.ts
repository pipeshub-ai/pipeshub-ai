/** A person a notice can reach by email: their address and the name it greets them by. */
export interface NoticeRecipient {
  email: string;
  name: string;
}

/**
 * The app's own ways to reach one person outside the app, for notices a bound feature sends: its mail path and a
 * direct message from an agent's Slack bot. Both throw when the deployment has no such path, so a caller can tell
 * an outage from a person who cannot be reached.
 */
export interface PersonNoticeChannels {
  /** The org's member, or null when they are gone or have no address. */
  person(orgId: string, userId: string): Promise<NoticeRecipient | null>;
  /** The app's public URL of an app path, for a link that leaves the app. */
  appUrl(path: string): string;
  /** One mail through the mail service's template `template`. */
  mail(mail: {
    orgId: string;
    to: NoticeRecipient;
    subject: string;
    template: string;
    data: Readonly<Record<string, unknown>>;
  }): Promise<void>;
  /** A direct message from the agent's own Slack bot; `skipped` when the agent has no bot or the person no account. */
  slack(message: {
    agentKey?: string;
    email: string;
    text: string;
  }): Promise<'sent' | 'skipped'>;
}
