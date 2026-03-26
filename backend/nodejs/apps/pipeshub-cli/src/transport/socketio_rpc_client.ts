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

export class SocketIoRpcClient {
  private socket: Socket | null = null;
  private connectPromise: Promise<void> | null = null;
  private nextId = 1;

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

  private toSocketBaseUrl(base: string): string {
    const override = process.env.PIPESHUB_WS_URL?.trim();
    if (override) return override.replace(/\/$/, "");
    const url = new URL(base);
    url.pathname = "";
    url.search = "";
    return url.toString().replace(/\/$/, "");
  }
}
