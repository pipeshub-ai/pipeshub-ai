import type { OrgSettings, SettingsReader } from '../../ports/org-settings';

/** The one store a deployment that holds one org keeps every org setting in. */
export function singleStoreOrgSettings(store: SettingsReader): OrgSettings {
  return { of: () => store };
}
