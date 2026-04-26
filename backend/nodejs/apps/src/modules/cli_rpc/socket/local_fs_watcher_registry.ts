import { DefaultEventsMap, Socket } from 'socket.io';
import { ConflictError } from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';

type CliRpcSocketData = {
  userId: string;
  orgId: string;
  watcherConnectorId?: string;
};

export type CliRpcSocket = Socket<
  DefaultEventsMap,
  DefaultEventsMap,
  DefaultEventsMap,
  CliRpcSocketData
>;

export type LocalFsResyncDispatchRequest = {
  orgId: string;
  connectorId: string;
  connectorName?: string;
  origin?: string;
  fullSync?: boolean;
};

type LocalFsResyncAck =
  | {
      ok: true;
      replayedBatches?: number;
      replayedEvents?: number;
      skippedBatches?: number;
    }
  | {
      ok: false;
      error?: {
        code?: string;
        message?: string;
      };
    };

const DEFAULT_RESYNC_TIMEOUT_MS = 90_000;

export class LocalFsWatcherRegistry {
  private readonly logger = Logger.getInstance({
    service: 'LocalFsWatcherRegistry',
  });

  private readonly watchers = new Map<string, CliRpcSocket>();

  register(socket: CliRpcSocket, connectorId: string): void {
    const orgId = String(socket.data.orgId ?? '').trim();
    const normalizedConnectorId = connectorId.trim();
    if (!orgId || !normalizedConnectorId) {
      throw new ConflictError('Watcher registration requires orgId and connectorId');
    }

    const key = this.toKey(orgId, normalizedConnectorId);
    const existing = this.watchers.get(key);
    if (existing && existing.id !== socket.id) {
      throw new ConflictError(
        'A Local FS watcher is already active for this connector. Stop the other `pipeshub run` first.',
      );
    }

    socket.data.watcherConnectorId = normalizedConnectorId;
    this.watchers.set(key, socket);
    this.logger.info('Registered Local FS watcher control socket', {
      orgId,
      connectorId: normalizedConnectorId,
      socketId: socket.id,
    });
  }

  unregister(socket: CliRpcSocket): void {
    const orgId = String(socket.data.orgId ?? '').trim();
    const connectorId = String(socket.data.watcherConnectorId ?? '').trim();
    if (!orgId || !connectorId) {
      return;
    }

    const key = this.toKey(orgId, connectorId);
    const current = this.watchers.get(key);
    if (current?.id !== socket.id) {
      return;
    }

    this.watchers.delete(key);
    socket.data.watcherConnectorId = undefined;
    this.logger.info('Unregistered Local FS watcher control socket', {
      orgId,
      connectorId,
      socketId: socket.id,
    });
  }

  hasActiveWatcher(orgId: string, connectorId: string): boolean {
    const o = String(orgId ?? '').trim();
    const c = connectorId.trim();
    if (!o || !c) {
      return false;
    }
    return this.watchers.has(this.toKey(o, c));
  }

  async dispatch(
    request: LocalFsResyncDispatchRequest,
    timeoutMs = DEFAULT_RESYNC_TIMEOUT_MS,
  ): Promise<{
    replayedBatches: number;
    replayedEvents: number;
    skippedBatches: number;
  }> {
    const key = this.toKey(request.orgId, request.connectorId);
    const socket = this.watchers.get(key);
    if (!socket) {
      throw new ConflictError(
        'No active Local FS watcher for this connector. Start `pipeshub run` first.',
      );
    }

    return new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        reject(
          new ConflictError(
            'Local FS watcher did not respond in time. Start `pipeshub run` again and retry.',
          ),
        );
      }, timeoutMs);

      const finish = (
        err: Error | null,
        ack?: LocalFsResyncAck,
      ): void => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);

        if (err) {
          reject(err);
          return;
        }
        if (!ack?.ok) {
          reject(
            new ConflictError(
              ack?.error?.message ||
                'Local FS watcher rejected the resync request.',
            ),
          );
          return;
        }

        resolve({
          replayedBatches: ack.replayedBatches ?? 0,
          replayedEvents: ack.replayedEvents ?? 0,
          skippedBatches: ack.skippedBatches ?? 0,
        });
      };

      socket
        .timeout(timeoutMs)
        .emit(
          'localfs:resync',
          {
            connectorId: request.connectorId,
            fullSync: request.fullSync === true,
            origin: request.origin ?? 'CONNECTOR',
          },
          (err: Error | null, ack?: LocalFsResyncAck) => finish(err, ack),
        );
    });
  }

  clear(): void {
    this.watchers.clear();
  }

  private toKey(orgId: string, connectorId: string): string {
    return `${orgId}:${connectorId}`;
  }
}

export const localFsWatcherRegistry = new LocalFsWatcherRegistry();
