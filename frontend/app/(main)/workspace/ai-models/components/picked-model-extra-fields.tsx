'use client';

import type { DiscoveredModel, PickedModel } from '../types';

/** Extra settings on a picked model's row when several are added at once. Editions plug in via config.ts. */
export function PickedModelExtraFields(_props: {
  item: PickedModel;
  discovered?: DiscoveredModel;
  /** Names of the provider's form fields, so a row only offers settings the provider has. */
  fieldNames: readonly string[];
  disabled: boolean;
  onChange: (patch: Partial<PickedModel>) => void;
}) {
  return null;
}
