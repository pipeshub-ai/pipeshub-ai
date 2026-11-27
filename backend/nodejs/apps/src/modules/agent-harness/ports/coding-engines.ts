import type { SessionRepository } from '../domain/repository';

/** What the org and the person stored as the default chat's coding engine (ENGPICK). */
export interface StoredCodingEngines {
  /** The person's choice: an engine kind or `none`; null: the org's default. */
  user: string | null;
  /** The org's default engine kind; null: none. */
  org: string | null;
  /** This chat's choice (SLASH3 `/engine`): an engine kind or `none`; null or absent: the person's. */
  session?: string | null;
  /** DEEPROUTE: whether the org lets a turn hand long work to its engine on its own; absent: on. */
  orgAutoRoute?: boolean;
}

/**
 * Where the default chat's coding-engine choices live: the person's on their
 * user document, the org's on the org document. Read at every Agent-mode
 * admission; throws when it cannot be read.
 */
export interface CodingEnginePreferences {
  read(orgId: string, userId: string): Promise<StoredCodingEngines>;
  /** null clears the person's choice (back to the org's default). */
  writeUser(
    orgId: string,
    userId: string,
    choice: string | null,
  ): Promise<void>;
  /** null: the org has no default engine. */
  writeOrg(orgId: string, engine: string | null): Promise<void>;
  /** DEEPROUTE: the org's "Hand long work to the coding engine" switch. */
  writeOrgAutoRoute(orgId: string, on: boolean): Promise<void>;
  /** A chat's own choice (SLASH3 `/engine`); null: none. */
  readSession(orgId: string, sessionId: string): Promise<string | null>;
  /** null clears the chat's choice (back to the person's). */
  writeSession(
    orgId: string,
    sessionId: string,
    choice: string | null,
  ): Promise<void>;
  /**
   * The engine version the chat's turns run on (C14.01, `kind@version`): the one its first engine turn
   * was admitted with, kept until the person picks an engine again; null: none yet.
   */
  readSessionPin(orgId: string, sessionId: string): Promise<string | null>;
  /** null clears it: the chat's next engine turn starts on the engine's default version. */
  writeSessionPin(
    orgId: string,
    sessionId: string,
    engine: string | null,
  ): Promise<void>;
  /** GIT-FLOW (WP-26): the repository the chat's engine works on; null: none. */
  readSessionRepository(
    orgId: string,
    sessionId: string,
  ): Promise<SessionRepository | null>;
  /** null clears it. */
  writeSessionRepository(
    orgId: string,
    sessionId: string,
    repository: SessionRepository | null,
  ): Promise<void>;
  /**
   * A person's erasure: removes their own choice in the org, live or already deleted; how many records it removed.
   * The org's erasure removes the org's and every person's with the org's stores.
   */
  eraseUser(orgId: string, userId: string): Promise<number>;
  /** A conversation's erasure: removes its choice, pin and repository; how many records it removed. */
  eraseSession(orgId: string, sessionId: string): Promise<number>;
}
