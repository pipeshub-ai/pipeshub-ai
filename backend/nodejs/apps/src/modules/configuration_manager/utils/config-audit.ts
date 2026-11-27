import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { audit, AuditFields } from '../../../libs/services/audit';
import { configPaths } from '../paths/paths';

/** The settings a store path belongs to, for the audit event name. No prefix lies under another. */
const SECTIONS: ReadonlyArray<readonly [string, string]> = [
  [configPaths.smtp, 'smtp'],
  [configPaths.storageService, 'storage'],
  [configPaths.auth.microsoft, 'auth_microsoft'],
  [configPaths.auth.google, 'auth_google'],
  [configPaths.auth.azureAD, 'auth_azure_ad'],
  [configPaths.auth.sso, 'auth_sso'],
  [configPaths.auth.oauth, 'auth_oauth'],
  [configPaths.auth.github, 'auth_github'],
  [configPaths.inheritance, 'inheritance'],
  [configPaths.platform.settings, 'platform'],
  [configPaths.slackBot, 'slack_bot'],
  [configPaths.systemPrompts, 'system_prompt'],
  [configPaths.aiModels, 'ai_models'],
  [configPaths.webSearch, 'web_search'],
  [configPaths.endpoint, 'public_urls'],
  [configPaths.metricsCollection, 'metrics'],
  [configPaths.aiGatewayIdentityPolicy, 'ai_identity_policy'],
  [configPaths.mcpAccessPolicy, 'mcp_access'],
  [configPaths.connectors.googleWorkspace.base, 'connector_google_workspace'],
  [configPaths.connectors.atlassian.base, 'connector_atlassian'],
  [configPaths.connectors.onedrive.base, 'connector_onedrive'],
  [configPaths.connectors.sharepoint.base, 'connector_sharepoint'],
];

/** The section a store path belongs to: the path itself or anything below it; `other` when none matches. */
export const configSection = (path: string): string =>
  SECTIONS.find(
    ([prefix]) =>
      path === prefix ||
      path.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`),
  )?.[1] ?? 'other';

export type ConfigAction = 'create' | 'update' | 'delete';

/**
 * One `config.<section>.<action>` audit line for a settings change an admin's request saved. `target` is shown only
 * when it is a PipesHub id, `reason` only as a snake_case code. Never a name, a field or a value.
 */
export const auditSettingsChange = (
  section: string,
  action: ConfigAction,
  fields: Pick<AuditFields, 'target' | 'reason'> = {},
): void => {
  audit(`config.${section}.${action}`, { ...fields, outcome: 'success' });
};

/** One `config.<section>.update|delete` audit line for a store write an admin made. Never the key or the value. */
export const auditConfigChange = (
  key: string,
  action: 'update' | 'delete',
): void => auditSettingsChange(configSection(key), action);

/** The store methods that change a setting. */
type ConfigWrites = Pick<
  KeyValueStoreService,
  'set' | 'delete' | 'compareAndSet'
>;

/**
 * The store an admin route writes through: each write the store accepted is one audit line. Every other
 * member is the real store's, bound to it, so a reconnect updates the shared store and never a copy.
 */
export const auditedConfigStore = <S extends ConfigWrites>(store: S): S => {
  const writes: ConfigWrites = {
    set: async (key: string, value: unknown): Promise<void> => {
      await store.set(key, value);
      auditConfigChange(key, 'update');
    },
    delete: async (key: string): Promise<void> => {
      await store.delete(key);
      auditConfigChange(key, 'delete');
    },
    compareAndSet: async <T>(
      key: string,
      expected: T | null,
      next: T,
    ): Promise<boolean> => {
      const wrote = await store.compareAndSet(key, expected, next);
      if (wrote) auditConfigChange(key, 'update');
      return wrote;
    },
  };
  return new Proxy(store, {
    get: (target, prop) => {
      // Own keys only: `in` would also match Object.prototype names such as `constructor`.
      if (Object.prototype.hasOwnProperty.call(writes, prop)) {
        return writes[prop as keyof typeof writes];
      }
      const value: unknown = Reflect.get(target, prop, target);
      return typeof value === 'function'
        ? (value as (...args: unknown[]) => unknown).bind(target)
        : value;
    },
  });
};
