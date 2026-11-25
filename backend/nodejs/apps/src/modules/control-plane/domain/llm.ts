/**
 * A configured model as a pin reads it (02-lld §8.1, the org tier's model
 * configs): who serves which names, never how it is reached. `configDigest`
 * covers the entry's non-secret members only.
 */
export interface ConfiguredLlm {
  readonly modelKey: string;
  /** As a pinned route writes it (the host's machine name). */
  readonly provider: string;
  readonly modelNames: readonly string[];
  readonly isDefault: boolean;
  readonly configDigest: string;
}

/** The model a run asks for, as its worker will look it up. */
export interface ModelChoice {
  readonly modelKey?: string;
  readonly modelName?: string;
  /** The provider that must serve it (its machine name): a model named by route, where two entries may list one name. */
  readonly provider?: string;
}

export interface SelectedLlm {
  readonly modelKey: string;
  readonly provider: string;
  readonly model: string;
  readonly configDigest: string;
}

const serves = (model: ConfiguredLlm, provider: string | undefined): boolean =>
  provider === undefined || model.provider === provider;

const byKey = (
  models: readonly ConfiguredLlm[],
  key: string,
  provider: string | undefined,
): ConfiguredLlm | undefined =>
  models.find((model) => model.modelKey === key && serves(model, provider));

const byName = (
  models: readonly ConfiguredLlm[],
  name: string,
  provider: string | undefined,
): ConfiguredLlm | undefined =>
  models.find(
    (model) => model.modelNames.includes(name) && serves(model, provider),
  );

/**
 * The configured entry a run's worker builds its model from, or null. A run
 * that names a model gets exactly it: a key that is not configured, or a
 * name its entry does not list, finds nothing (refuse, never another model);
 * a provider named with it must be the entry's.
 * A run that names none gets the default entry, else the first, on that
 * entry's first name. Null also when no model is configured.
 */
export function selectLlm(
  models: readonly ConfiguredLlm[],
  choice: ModelChoice = {},
): SelectedLlm | null {
  const { modelKey, modelName, provider } = choice;
  const named = modelKey !== undefined || modelName !== undefined;
  const entry =
    modelKey !== undefined
      ? byKey(models, modelKey, provider)
      : modelName !== undefined
        ? byName(models, modelName, provider)
        : (models.find((model) => model.isDefault) ?? models[0]);
  const model = named
    ? (modelName ?? entry?.modelNames[0])
    : entry?.modelNames[0];
  if (entry === undefined || model === undefined) return null;
  if (!entry.modelNames.includes(model)) return null;
  return {
    modelKey: entry.modelKey,
    provider: entry.provider,
    model,
    configDigest: entry.configDigest,
  };
}

/**
 * GA-15: the reserved `models.roles` keys that name an engine role rather
 * than a sub-agent role; the harness's `ENGINE_ROLES` (domain/model-roles.ts)
 * is the same list.
 */
export const ENGINE_MODEL_ROLES = ['fast', 'subagent', 'compaction'] as const;

/** `<provider>:<model>`: how a model is named in `models.routes`, `models.primary` and `models.roles`. */
export const llmRoute = (provider: string, model: string): string =>
  `${provider}:${model}`;

/** Every model the deployment configures, as routes, sorted. */
export const configuredRoutes = (models: readonly ConfiguredLlm[]): string[] =>
  [
    ...new Set(
      models.flatMap((model) =>
        model.modelNames.map((name) => llmRoute(model.provider, name)),
      ),
    ),
  ].sort();
