import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { endpoint } from '../constants/constants';

/**
 * The address the key-value store holds for `service` ("storage", "cm", ...),
 * or `fallback` when it holds none. Every service shares that one document, so
 * any entry in it may be missing or not yet written.
 */
export const storedServiceEndpoint = async (
  keyValueStoreService: KeyValueStoreService,
  service: string,
  fallback: string,
): Promise<string> => {
  const stored = await keyValueStoreService.get<string>(endpoint);
  const parsed: unknown =
    typeof stored === 'string' && stored !== '' ? JSON.parse(stored) : null;
  const entry: unknown =
    parsed !== null && typeof parsed === 'object'
      ? (parsed as Record<string, unknown>)[service]
      : undefined;
  const value: unknown =
    entry !== null && typeof entry === 'object'
      ? (entry as { endpoint?: unknown }).endpoint
      : undefined;
  return typeof value === 'string' && value !== '' ? value : fallback;
};
