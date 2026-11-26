/**
 * Why the model picker shows configured models as unavailable beyond the reasons it knows itself: an extension
 * point (`@/config`). Given the `modelChoices` of the model access answer, it answers a translation key per
 * choice (`modelChoiceKey`) to show beside it; such a model cannot be picked. This build adds none.
 */
export type ModelUnavailableReasons = (choices: unknown) => ReadonlyMap<string, string>;

export const modelUnavailableReasons: ModelUnavailableReasons = () => new Map();
