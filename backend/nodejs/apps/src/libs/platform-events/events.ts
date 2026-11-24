/**
 * The catalogue of platform events: every name application code may emit, and
 * what each carries. Emitters and listeners are both checked against it.
 *
 * Naming: product terms, dot-separated, ending in what happened (past tense).
 *
 * Payloads: what the product knows at that point (ids, enumerated values,
 * counts, durations). Never document or query content, never a secret, never a
 * whole request, and never a value shaped for one particular listener. A
 * listener receives the same object the emitter built and must not change it.
 *
 * A build that adds events of its own extends the map by declaration merging:
 *
 *   declare module '<relative path>/libs/platform-events/events' {
 *     interface PlatformEvents { 'thing.happened': { thingId: string } }
 *   }
 */

/** Which client sent a request, as far as it said so itself. */
export interface RequestClient {
  /** First product token of the User-Agent (`name/version`), when well formed. */
  userAgent?: string;
  /** Value of `X-PipesHub-Channel`, when it is one of our channel tags. */
  channel?: string;
}

/** Who made an MCP request, and how they authenticated. */
export interface McpCaller extends RequestClient {
  orgId?: string;
  userId?: string;
  /** Domain part of the caller's address (`unknown` when there is none); never the address. */
  emailDomain: string;
  authType: 'pat' | 'oauth' | 'session';
  /** The OAuth app, for a token an app obtained (not a PAT or service token). */
  oauthClientId?: string;
}

export interface PlatformEvents {
  // ---- accounts and organisations -----------------------------------------

  /** A user signed in. */
  'user.signed_in': {
    orgId?: string;
    userId?: string;
    email?: string;
    /** True when this user had never signed in before. */
    firstSignIn: boolean;
    /** The sign-in method that completed (`password`, `otp`, `google`, ...). */
    authMethod: string;
  };

  /** An invited user finished setting up their account. */
  'user.account_setup.completed': {
    orgId?: string;
    userId?: string;
    email?: string;
  };

  /** An organisation was created together with its first administrator. */
  'org.created': {
    orgId: unknown;
    adminUserId: unknown;
    contactEmail: string;
    adminFullName: string;
    /** Channel tag of the sign-up link, or `direct`. */
    signupChannel: string;
    /** Domain of the site that referred a direct sign-up, when known. */
    signupReferrerDomain?: string;
  };

  /**
   * An administrator switched the organisation's Data Collection setting.
   * Emitted before the new value is stored.
   */
  'org.data_collection_setting.changed': {
    orgId: unknown;
    enabled: boolean;
  };

  // ---- programmatic access ------------------------------------------------

  /** A user created a personal access token. */
  'personal_access_token.created': RequestClient & {
    orgId: string;
    userId: string;
    emailDomain: string;
    /** How many scopes the token holds (the scopes themselves are not listed). */
    scopeCount: number;
    /** The lifetime the token was minted with. */
    expiryDays: number;
  };

  /** A user changed which knowledge a personal access token can reach. */
  'personal_access_token.knowledge_limit.changed': RequestClient & {
    orgId: string;
    userId: string;
    emailDomain: string;
    /** False when the limit was removed. */
    limited: boolean;
    /** How many items the limit names (the items themselves are not listed). */
    itemCount: number;
  };

  /** The OAuth token endpoint issued a first token for a grant (not a refresh). */
  'oauth.token.issued': RequestClient & {
    grantType: string;
    clientId: string;
  };

  /** An MCP client completed `initialize`. */
  'mcp.client.connected': McpCaller & {
    clientName?: string;
    clientVersion?: string;
  };

  /** An MCP `tools/call` request was served (not necessarily successfully). */
  'mcp.tool.called': McpCaller & {
    /** Tool name only; arguments are never included. */
    tool?: string;
  };

  // ---- relational database ------------------------------------------------

  /** One SQL statement finished, successfully or not. */
  'db.query.completed': {
    /** The service the unit of work runs as. */
    service: string;
    /** The statement's call-site tag (`pipeshub:<site>`), or `untagged`. */
    site: string;
    access: 'read' | 'write';
    durationSeconds: number;
  };

  /** A unit of work ended, retries included. */
  'db.unit_of_work.completed': {
    service: string;
    /** `commit`, or the class of failure that ended it. */
    outcome: string;
    durationSeconds: number;
  };

  /** A unit of work is being retried. */
  'db.unit_of_work.retried': {
    /** The SQLSTATE that caused it, or `connection` for a lost connection. */
    cause: string;
  };

  /** A unit of work was refused because no organisation context was bound. */
  'db.unit_of_work.refused': { reason: 'org_context_missing' };

  /** A pool connection was handed out. */
  'db.connection.acquired': { waitSeconds: number };

  /** A pooled connection arrived with a session role already set and was destroyed. */
  'db.connection.session_role_leaked': Record<string, never>;

  // ---- document store -----------------------------------------------------

  /** A write was refused by the store fence. */
  'docstore.write.refused': {
    store: 'mongo' | 'postgres';
    reason: 'unreadable' | 'deadline' | 'readonly' | 'retired' | 'epoch';
  };

  /** The Mongo fence was read before a write. */
  'docstore.fence.checked': { durationSeconds: number };

  /** The database refused a write for privilege or row policy. */
  'docstore.row_policy.violated': Record<string, never>;

  /** A statement ran after an error inside a unit of work without a savepoint. */
  'docstore.transaction.failed': Record<string, never>;

  /** U+0000 characters were removed from a value before a write. */
  'docstore.nul_characters.stripped': {
    repo: string;
    field: string;
    count: number;
  };

  /** A shadow read (DOC_STORE=shadow) was compared with the primary's. */
  'docstore.shadow_read.compared': {
    repo: string;
    /** `equal`, `diff`, `timeout` or `error`. */
    result: string;
  };

  /** The TTL sweeper removed rows from one target. */
  'docstore.ttl.swept': { target: string; removed: number };

  /** A TTL sweep of one target failed. */
  'docstore.ttl.sweep_failed': { target: string };

  // ---- messaging ----------------------------------------------------------

  /** A consumer-inbox read or write failed; the message ran as if new. */
  'consumer.inbox.failed': { operation: string };

  /** A redelivered broker message was acknowledged without running again. */
  'consumer.delivery.skipped': { consumer: string };

  /** A notification event was refused before any write. */
  'notification.event.refused': { reason: string };
}

export type PlatformEventName = keyof PlatformEvents;

export type PlatformEventPayload<K extends PlatformEventName> =
  PlatformEvents[K];
