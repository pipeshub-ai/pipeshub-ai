/**
 * GIT-FLOW (WP-26): the GitHub toolset instances a person may use, as the toolset store (the connectors service)
 * answers with the person's own credential. A repository's `toolsetInstanceId` is checked here, never trusted
 * from the client.
 */
export interface RepositoryToolsets {
  /**
   * Whether `instanceId` is a GitHub toolset instance the caller may use (theirs, or one of the org's) and is
   * signed in to. Throws when the store cannot be asked.
   */
  usableGithubInstance(request: {
    orgId: string;
    userId: string;
    instanceId: string;
    authorization: string;
  }): Promise<boolean>;
}
