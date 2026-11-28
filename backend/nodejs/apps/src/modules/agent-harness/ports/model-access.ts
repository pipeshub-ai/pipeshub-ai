import type { PolicyRevisions } from './policy-revisions';
import type { OrgAccessPolicy } from '../domain/model-access';

/**
 * Where a configured model's calls go, as its configuration says (MG-15):
 * the non-secret fields the region is derived from.
 */
export interface ModelLocation {
  region?: string;
  location?: string;
  endpoint?: string;
}

/** One configured model (`aiModels` entry): its provider as configured, its key and model names. */
export interface ConfiguredModel {
  provider: string;
  modelKey: string;
  modelNames: string[];
  /** MG-15: absent when the source does not read it (every route's region is then unknown). */
  location?: ModelLocation;
}

/**
 * The org's configured models (A1): admission pins an engine run's concrete
 * model from them, never `org-default`. Throws when they cannot be read.
 */
export interface ConfiguredModels {
  /** The entry with this key, or the org's default (`isDefault`) when no key is given; null when none. */
  find(
    orgId: string,
    modelKey: string | undefined,
  ): Promise<ConfiguredModel | null>;
  /** Every configured entry, as configured (MA1: what an agent may name). Absent: this source cannot list them. */
  list?(orgId: string): Promise<ConfiguredModel[]>;
  /**
   * MG-15: the embedding entry in use (the `isDefault` one, else the first;
   * the built-in model, provider `default`, when none is configured).
   * Absent: this source cannot read it.
   */
  embedding?(orgId: string): Promise<ConfiguredModel>;
}

/**
 * Each org's narrowing of the signed vendor rules, read afresh at every
 * admission. Throws when it cannot be read: never read as "nothing narrowed".
 */
export interface ModelAccessPolicies extends Partial<PolicyRevisions> {
  read(orgId: string): Promise<OrgAccessPolicy>;
}

/**
 * Fields an extension shows beside the model choices: on the view and on each
 * choice. They are added beside the view's own members and never replace one.
 */
export interface ModelChoicesPlacement {
  /** Fields on the view, shown with the choices. */
  view: Readonly<Record<string, unknown>>;
  /** One choice's fields; undefined: none shown for it. */
  of(
    entry: ConfiguredModel,
    modelName: string,
  ): Readonly<Record<string, unknown>> | undefined;
}

/**
 * What an org's model choices show beyond the view's own members: an extension
 * point, bound to `AGENT_HARNESS_MODEL_PLACEMENT`. Unbound, the model choices
 * carry nothing more.
 */
export interface ModelPlacementSource {
  /** Throws when it cannot be read: never read as "nothing more". */
  choices(orgId: string): Promise<ModelChoicesPlacement>;
}
