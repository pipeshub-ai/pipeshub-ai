import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { canonicalDigest } from '../../agent-harness/contracts/canonical-json';
import {
  actorRefOfUser,
  orgScope,
  type ControlPlaneTransaction,
  type DeploymentRevision,
} from '../../control-plane';
import { configPaths } from '../paths/paths';
import { AiModelsConfigRepository } from '../services/aiModelsConfig.repository';

/** Who saves: the authenticated admin of the request. */
export interface AiModelsSaver {
  user?: { userId?: unknown; orgId?: unknown };
}

/**
 * Stores the AI models document if the stored ciphertext is still `expected`
 * (false, and nothing recorded, when another writer got there first).
 * `models` is the document as saved (plain), read only for what the audit
 * entry may name; `changed` the model types the save touched.
 */
export type AiModelsSave = (
  by: AiModelsSaver,
  expected: string | null,
  encrypted: string,
  models: unknown,
  changed: readonly string[],
) => Promise<boolean>;

/** The AI models repository whose writes go through `save`, as `by`, naming `changed`. */
export const aiModelsRepository = (
  store: KeyValueStoreService,
  save: AiModelsSave,
  by: AiModelsSaver,
  changed: readonly string[],
): AiModelsConfigRepository =>
  new AiModelsConfigRepository(store, (expected, encrypted, models) =>
    save(by, expected, encrypted, models, changed),
  );

/** A lost compare-and-set inside the control-plane write: aborts it, so nothing is recorded. */
class AiModelsWriteLost extends Error {}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * What an audit entry may be a digest of: per model type each entry's key,
 * provider and default mark. Never an entry's `configuration`, where the
 * keys and endpoints are.
 */
export function aiModelsOutline(models: unknown): Record<string, unknown> {
  if (!isRecord(models)) return {};
  return Object.fromEntries(
    Object.entries(models)
      .filter((entry): entry is [string, unknown[]] => Array.isArray(entry[1]))
      .map(([type, entries]) => [
        type,
        entries.filter(isRecord).map((entry) => ({
          modelKey: entry['modelKey'] ?? null,
          provider: entry['provider'] ?? null,
          isDefault: entry['isDefault'] === true,
        })),
      ]),
  );
}

/** The store alone: no audit entry, no head moved (callers that own no control plane). */
export const plainAiModelsSave =
  (store: KeyValueStoreService): AiModelsSave =>
  (_by, expected, encrypted) =>
    store.compareAndSet<string>(configPaths.aiModels, expected, encrypted);

/**
 * An AI-model save as one control-plane write (CPD-6): one `config.changed`
 * on the saving org's chain, its head moved, and the deployment's revision
 * moved with it, because every org's pins read this one document. The store
 * write is the transaction's effect, so a save that fails records nothing.
 */
export const auditedAiModelsSave =
  (
    store: KeyValueStoreService,
    controlPlane: ControlPlaneTransaction,
    deployment: DeploymentRevision,
  ): AiModelsSave =>
  async (by, expected, encrypted, models, changed) => {
    const orgId = String(by.user?.orgId);
    try {
      await controlPlane.run(
        { orgId, actor: actorRefOfUser(String(by.user?.userId)) },
        async (tx) => {
          await deployment.move(tx);
          return {
            result: tx.external(async () => {
              if (
                !(await store.compareAndSet<string>(
                  configPaths.aiModels,
                  expected,
                  encrypted,
                ))
              ) {
                throw new AiModelsWriteLost();
              }
            }),
            change: {
              scope: orgScope(orgId),
              object: { type: 'ai-models', id: 'aiModels' },
              revisionFrom: null,
              revisionTo: null,
              changeDigest: canonicalDigest(aiModelsOutline(models)),
              changed: [...changed].sort(),
            },
          };
        },
      );
    } catch (error) {
      if (error instanceof AiModelsWriteLost) return false;
      throw error;
    }
    return true;
  };
