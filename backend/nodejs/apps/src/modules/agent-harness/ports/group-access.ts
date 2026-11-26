import type { AccessGroupsPolicy } from '../../control-plane';

/**
 * The actor's membership of the org's user groups (G-41), read live at each
 * call: a removal is seen by the very next admission. Throws when it cannot
 * be read, never answers "not a member" for an outage.
 */
export interface ActorGroupMembership {
  /** Whether `userId` is in any of the org's live groups `groupIds` names; false for an empty list. */
  memberOfAny(
    orgId: string,
    userId: string,
    groupIds: readonly string[],
  ): Promise<boolean>;
}

/** The org layer's `access` section (G-41); null: none. Throws when the org's policy cannot be read. */
export interface AccessGroupsSource {
  read(orgId: string): Promise<AccessGroupsPolicy | null>;
}

/**
 * The names of the org's live user groups among the ids a page holds, in one
 * read (ADR-UI-03); any other id is absent. Throws when they cannot be read.
 */
export interface GroupNameReader {
  groupNames(
    orgId: string,
    groupIds: readonly string[],
  ): Promise<Map<string, string>>;
}

/** A subject an org may limit to some of its groups (G-41). */
export type GroupSubject =
  | { kind: 'harness' }
  | { kind: 'engine'; engine: string }
  | { kind: 'model'; modelKey: string | undefined };

/**
 * Who may use the harness, each coding engine and each configured model, by
 * the org's user groups (G-41). Bound by an extension; unbound, nothing is
 * limited by group. Anything that cannot be read refuses
 * (`HarnessUnavailableError`), never admits.
 */
export interface GroupAccess {
  /** The org's limits, read once for the checks of one admission; null: none. */
  policy(orgId: string): Promise<AccessGroupsPolicy | null>;
  /** `policy`: what `policy(orgId)` read for this admission, so one admission reads it once. */
  admits(
    orgId: string,
    userId: string,
    subject: GroupSubject,
    policy?: AccessGroupsPolicy | null,
  ): Promise<boolean>;
  /** The configured models (by key) `userId` may not use, for the pickers to grey out. */
  refusedModels(orgId: string, userId: string): Promise<Set<string>>;
  /** The names of the configured models `userId` may use, for a refusal to name; undefined: unknown here. */
  allowedModelNames(
    orgId: string,
    userId: string,
    provider?: string,
  ): Promise<string[] | undefined>;
}

/** A turn today's loop is about to serve, as the chat routes know it. */
export interface ChatModelTurn {
  orgId: string;
  actorId: string;
  /** The agent the turn talks to; null: the assistant. */
  agentKey: string | null;
  /** The model the request names; absent: the agent's first model, else the org's default. */
  modelKey: string | undefined;
  /** The sender's own credential, to read which models the agent grants. */
  authorization?: string;
}

/** The model groups on a turn today's loop serves (G-41); unbound, every turn is admitted. */
export interface ChatModelGroups {
  admits(turn: ChatModelTurn): Promise<boolean>;
}
