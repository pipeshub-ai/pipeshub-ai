import { io, Socket } from "socket.io-client";

type RpcRequest = {
  type: "request";
  id: string;
  op: "restProxy";
  payload: {
    method: string;
    path: string;
    query?: Record<string, string | number | boolean | null | undefined>;
    body?: unknown;
  };
};

type RpcResponse =
  | {
      type: "response";
      id: string;
      ok: true;
      result: { status: number; body: unknown };
    }
  | {
      type: "response";
      id: string;
      ok: false;
      error: { code: string; message: string; status?: number };
    };

const DEFAULT_TIMEOUT_MS = 90_000;
const SOCKET_PATH = "/socket.io-cli-rpc";

type FolderSyncRegisterAck =
  | { ok: true }
  | {
      type: "response";
      id: string;
      ok: false;
      error: { code: string; message: string; status?: number };
    };

export type FolderSyncResyncRequest = {
  connectorId: string;
  fullSync?: boolean;
  origin?: string;
};

type FolderSyncResyncAck =
  | {
      ok: true;
      replayedBatches?: number;
      replayedEvents?: number;
      skippedBatches?: number;
    }
  | {
      ok: false;
      error?: { code?: string; message?: string };
    };

export class SocketIoRpcClient {
  private socket: Socket | null = null;
  private connectPromise: Promise<void> | null = null;
  private nextId = 1;
  private watcherConnectorId: string | null = null;
  private folderSyncResyncListener:
    | ((request: FolderSyncResyncRequest) => Promise<{
        replayedBatches?: number;
        replayedEvents?: number;
        skippedBatches?: number;
      }>)
    | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly token: string
  ) {}

  async request(
    payload: RpcRequest["payload"],
    timeoutMs = DEFAULT_TIMEOUT_MS
  ): Promise<{ status: number; body: unknown }> {
    await this.ensureConnected();
    const id = String(this.nextId++);
    const request: RpcRequest = { type: "request", id, op: "restProxy", payload };

    return new Promise<{ status: number; body: unknown }>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Socket RPC timeout after ${timeoutMs}ms`));
      }, timeoutMs);
      this.socket!.emit("rpc:request", request, (response: RpcResponse) => {
        clearTimeout(timer);
        if (!response || response.type !== "response" || response.id !== id) {
          reject(new Error("Invalid RPC response envelope"));
          return;
        }
        if (response.ok) {
          resolve(response.result);
          return;
        }
        const status = response.error.status != null ? ` (status ${response.error.status})` : "";
        reject(new Error(`${response.error.code}${status}: ${response.error.message}`));
      });
    });
  }

  async registerFolderSyncWatcher(connectorId: string): Promise<void> {
    await this.ensureConnected();
    const normalizedConnectorId = connectorId.trim();
    this.watcherConnectorId = normalizedConnectorId;
    await this.emitWatcherRegistration(normalizedConnectorId);
  }

  async onFolderSyncResync(
    handler: (request: FolderSyncResyncRequest) => Promise<{
      replayedBatches?: number;
      replayedEvents?: number;
      skippedBatches?: number;
    }>
  ): Promise<void> {
    await this.ensureConnected();
    this.folderSyncResyncListener = handler;
    this.socket!.off("foldersync:resync");
    this.socket!.on(
      "foldersync:resync",
      async (
        request: FolderSyncResyncRequest,
        ack?: (response: FolderSyncResyncAck) => void
      ) => {
        try {
          const result = await handler(request);
          ack?.({
            ok: true,
            replayedBatches: result.replayedBatches ?? 0,
            replayedEvents: result.replayedEvents ?? 0,
            skippedBatches: result.skippedBatches ?? 0,
          });
        } catch (error) {
          ack?.({
            ok: false,
            error: {
              code: "RESYNC_FAILED",
              message: error instanceof Error ? error.message : String(error),
            },
          });
        }
      }
    );
  }

  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
    this.connectPromise = null;
  }

  private async ensureConnected(): Promise<void> {
    if (this.socket && this.socket.connected) return;
    if (this.connectPromise) return this.connectPromise;

    this.connectPromise = new Promise<void>((resolve, reject) => {
      const url = this.toSocketBaseUrl(this.baseUrl);
      const socket = io(`${url}/cli-rpc`, {
        auth: { token: `Bearer ${this.token}` },
        transports: ["websocket"],
        timeout: DEFAULT_TIMEOUT_MS,
        path: SOCKET_PATH,
      });
      this.socket = socket;

      socket.on("connect", () => {
        this.connectPromise = null;
        void this.restoreControlState();
        resolve();
      });
      socket.on("connect_error", (err) => {
        const message = err instanceof Error ? err.message : String(err);
        this.connectPromise = null;
        reject(new Error(message));
      });
      socket.on("disconnect", () => {
        this.socket = null;
      });
    });
    return this.connectPromise;
  }

  private async restoreControlState(): Promise<void> {
    if (!this.socket || !this.socket.connected) {
      return;
    }
    if (this.folderSyncResyncListener) {
      await this.onFolderSyncResync(this.folderSyncResyncListener);
    }
    if (this.watcherConnectorId) {
      await this.emitWatcherRegistration(this.watcherConnectorId);
    }
  }

  private async emitWatcherRegistration(connectorId: string): Promise<void> {
    await this.ensureConnected();
    return new Promise<void>((resolve, reject) => {
      this.socket!.emit(
        "foldersync:registerWatcher",
        { connectorId },
        (response: FolderSyncRegisterAck) => {
          if (response && "ok" in response && response.ok === true) {
            resolve();
            return;
          }
          const failure =
            response && "error" in response ? response.error : undefined;
          const status = failure?.status != null ? ` (status ${failure.status})` : "";
          reject(
            new Error(
              `${failure?.code ?? "WATCHER_REGISTRATION_FAILED"}${status}: ${
                failure?.message ?? "Folder Sync watcher registration failed"
              }`
            )
          );
        }
      );
    });
  }

  private toSocketBaseUrl(base: string): string {
    const override = process.env.PIPESHUB_WS_URL?.trim();
    if (override) return override.replace(/\/$/, "");
    const url = new URL(base);
    url.pathname = "";
    url.search = "";
    return url.toString().replace(/\/$/, "");
  }
}
