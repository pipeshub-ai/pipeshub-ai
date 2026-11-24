/**
 * Questions application code may ask of a plug-in without importing it.
 *
 * `getCapability(name)` returns `undefined` when no loaded plug-in answers, and
 * the caller must then behave as a build that does not have the feature.
 * Keep each capability narrow and named for the product feature, not for the
 * plug-in that happens to provide it.
 */
export interface PlatformCapabilities {
  'data-collection': DataCollectionCapability;
}

/** Whether this build can send usage data anywhere at all. */
export interface DataCollectionCapability {
  /** False when the deployment has switched collection off for the whole process. */
  isActive(): boolean;
}
