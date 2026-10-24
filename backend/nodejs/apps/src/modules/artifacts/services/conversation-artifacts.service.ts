import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { scopedStorageServiceJwtGenerator } from '../../../libs/utils/createJwt';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  executeConnectorCommand,
  handleBackendError,
} from '../../tokens_manager/utils/connector.utils';

// A conversation can hold many artifacts, each a storage purge plus graph deletes.
const CLEANUP_TIMEOUT_MS = 120_000;

/**
 * Deletes every artifact the caller owns in a conversation, and the files the
 * assistant stored for it, before the conversation itself is deleted. Throws
 * when that could not be done, so the conversation is not deleted and the
 * user's retry finishes the cleanup.
 *
 * The caller must already be known to own the conversation: the folder purge
 * below relies on it, since the storage service cannot check it.
 */
export async function deleteConversationArtifacts(
  appConfig: AppConfig,
  conversationId: string,
  req: AuthenticatedUserRequest,
): Promise<void> {
  const operation = 'delete conversation artifacts';
  const id = encodeURIComponent(conversationId);
  try {
    // The caller's own token: the connectors service deletes only what they own.
    const authorization = req.headers.authorization;
    const response = await executeConnectorCommand<{ kept?: number }>(
      `${appConfig.connectorBackend}/api/v1/artifacts/conversations/${id}`,
      HttpMethod.DELETE,
      { authorization: typeof authorization === 'string' ? authorization : '' },
      undefined,
      { timeoutMs: CLEANUP_TIMEOUT_MS },
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw handleBackendError(response, operation);
    }

    // Older outputs sit in the conversation's storage folders with no graph
    // record. Leave the folders when someone else still owns an artifact of the
    // conversation: their file may be filed there too.
    if (response.data?.kept !== 0) {
      return;
    }
    const storageToken = scopedStorageServiceJwtGenerator(
      String(req.user?.orgId),
      appConfig.scopedJwtSecret,
      String(req.user?.userId),
    );
    const purge = await executeConnectorCommand(
      `${appConfig.storage.endpoint}/api/v1/document/internal/conversations/${id}/purge`,
      HttpMethod.DELETE,
      { authorization: `Bearer ${storageToken}` },
      undefined,
      { timeoutMs: CLEANUP_TIMEOUT_MS },
    );
    if (purge.statusCode < 200 || purge.statusCode >= 300) {
      throw handleBackendError(purge, operation);
    }
  } catch (error: unknown) {
    throw handleBackendError(error, operation);
  }
}
