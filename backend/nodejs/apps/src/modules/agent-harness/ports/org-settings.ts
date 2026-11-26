/** A reader of the configuration store's documents, as the configuration manager's readers take it. */
export interface SettingsReader {
  get<T>(key: string): Promise<T | null>;
}

/**
 * Where the settings an org owns are read (its AI models, its platform and Labs settings), for the org each call
 * names. A deployment that holds one org keeps them in its one store; an edition whose orgs keep their own answers
 * for the org named. The org is always passed, never taken from the request: a schedule firing or a worker's spawn
 * has no request.
 */
export interface OrgSettings {
  of(orgId: string): SettingsReader;
}
