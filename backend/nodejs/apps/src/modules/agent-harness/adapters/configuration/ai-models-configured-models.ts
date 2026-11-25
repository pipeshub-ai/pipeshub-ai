import { readStoredAiModelsConfig } from '../../../configuration_manager/utils/util';
import type { OrgSettings } from '../../ports/org-settings';
import {
  ConfiguredModel,
  ConfiguredModels,
  type ModelLocation,
} from '../../ports/model-access';

/**
 * The org's configured LLMs (`aiModels.llm`), as the model gateway reads them
 * (`ConfiguredModelAccess`): an entry by `modelKey`, or the one marked
 * `isDefault` when no key is given, never the first entry. The model names
 * are the entry's comma-separated `configuration.model`.
 */
export class AiModelsConfiguredModels implements ConfiguredModels {
  constructor(private readonly settings: OrgSettings) {}

  async find(
    orgId: string,
    modelKey: string | undefined,
  ): Promise<ConfiguredModel | null> {
    const found = (await this.entries(orgId)).find((entry) =>
      modelKey === undefined
        ? entry['isDefault'] === true
        : entry['modelKey'] === modelKey,
    );
    return found === undefined ? null : configuredModel(found);
  }

  async list(orgId: string): Promise<ConfiguredModel[]> {
    return (await this.entries(orgId))
      .map(configuredModel)
      .filter((model): model is ConfiguredModel => model !== null);
  }

  async embedding(orgId: string): Promise<ConfiguredModel> {
    const entries = await this.entries(orgId, 'embedding');
    const chosen =
      entries.find((entry) => entry['isDefault'] === true) ?? entries[0];
    const found =
      chosen === undefined
        ? null
        : configuredModel({ modelKey: 'embedding', ...chosen });
    return (
      found ?? {
        provider: 'default',
        modelKey: 'default',
        modelNames: [DEFAULT_EMBEDDING_MODEL],
      }
    );
  }

  private async entries(
    orgId: string,
    bucket: 'llm' | 'embedding' = 'llm',
  ): Promise<Record<string, unknown>[]> {
    const config = await readStoredAiModelsConfig(this.settings.of(orgId));
    const entries = config?.[bucket];
    return Array.isArray(entries)
      ? entries.filter(
          (entry): entry is Record<string, unknown> =>
            typeof entry === 'object' && entry !== null,
        )
      : [];
  }
}

/** What the built-in embedding server runs when no embedding model is configured (Python's DEFAULT_EMBEDDING_MODEL). */
export const DEFAULT_EMBEDDING_MODEL = 'BAAI/bge-large-en-v1.5';

function configuredModel(
  entry: Record<string, unknown>,
): ConfiguredModel | null {
  const provider = entry['provider'];
  const key = entry['modelKey'];
  const configuration = entry['configuration'];
  const names =
    typeof configuration === 'object' &&
    configuration !== null &&
    typeof (configuration as Record<string, unknown>)['model'] === 'string'
      ? ((configuration as Record<string, unknown>)['model'] as string)
          .split(',')
          .map((name) => name.trim())
          .filter((name) => name.length > 0)
      : [];
  if (typeof provider !== 'string' || typeof key !== 'string') return null;
  const location = locationOf(configuration);
  return {
    provider,
    modelKey: key,
    modelNames: names,
    ...(location !== null ? { location } : {}),
  };
}

/** The non-secret fields a model's region is derived from (MG-15), null when none is set; a credential is never read here. */
function locationOf(configuration: unknown): ModelLocation | null {
  if (typeof configuration !== 'object' || configuration === null) return null;
  const fields = configuration as Record<string, unknown>;
  const location: ModelLocation = {};
  for (const field of ['region', 'location', 'endpoint'] as const) {
    const value = fields[field];
    if (typeof value === 'string' && value.trim() !== '')
      location[field] = value;
  }
  return Object.keys(location).length > 0 ? location : null;
}
