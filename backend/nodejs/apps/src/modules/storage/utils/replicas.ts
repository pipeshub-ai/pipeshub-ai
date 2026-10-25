import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { storageTypes } from '../../configuration_manager/constants/constants';
import { storageEtcdPaths } from '../constants/constants';

/**
 * Local storage keeps files on the app's own disk. With more than one app
 * replica (Helm) each pod has a different disk, so files saved by one pod are
 * missing on the others and vanish when a pod restarts. The Helm chart tells
 * the app how many replicas it may run as through PIPESHUB_APP_MAX_REPLICAS.
 */
export function appReplicaCeiling(
  env: NodeJS.ProcessEnv = process.env,
): number {
  const replicas = Number.parseInt(env.PIPESHUB_APP_MAX_REPLICAS ?? '', 10);
  return Number.isFinite(replicas) && replicas > 1 ? replicas : 1;
}

export function localStorageReplicaProblem(
  env: NodeJS.ProcessEnv = process.env,
): string | null {
  const replicas = appReplicaCeiling(env);
  if (replicas <= 1) return null;
  return (
    `Local storage keeps files on each server's own disk, and this deployment runs up to ` +
    `${replicas} app replicas: each would see different files, and files are lost when a ` +
    `replica restarts. Use S3 or Azure Blob storage, or run a single replica.`
  );
}

/** The replica problem when the saved storage is local (or none is saved yet, which means local). */
export async function configuredLocalStorageReplicaProblem(
  keyValueStoreService: KeyValueStoreService,
  env: NodeJS.ProcessEnv = process.env,
): Promise<string | null> {
  const problem = localStorageReplicaProblem(env);
  if (!problem) return null;
  const raw =
    (await keyValueStoreService.get<string>(storageEtcdPaths)) || '{}';
  const storageType = (JSON.parse(raw) as { storageType?: string }).storageType;
  return !storageType || storageType === storageTypes.LOCAL ? problem : null;
}
