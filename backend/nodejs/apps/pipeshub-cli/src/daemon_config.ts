import * as fs from "fs";
import * as path from "path";
import { FernetFileTokenStore } from "./token_store";

export const DAEMON_CONFIG_FILENAME = "daemon.json";

export type DaemonConfig = {
  sync_root: string;
  connector_instance_id: string;
  /** Mirrors last `setup`; used when GET /config omits include_subfolders. */
  include_subfolders?: boolean;
};

export function emptyDaemonConfig(): DaemonConfig {
  return { sync_root: "", connector_instance_id: "" };
}

export function daemonConfigComplete(dc: DaemonConfig): boolean {
  return Boolean(dc.sync_root.trim() && dc.connector_instance_id.trim());
}

function configPath(authDir?: string): string {
  const base = authDir
    ? path.resolve(authDir)
    : path.dirname(new FernetFileTokenStore().path);
  return path.join(base, DAEMON_CONFIG_FILENAME);
}

function parseOptionalBool(v: unknown): boolean | undefined {
  if (typeof v === "boolean") {
    return v;
  }
  if (v === "true" || v === "false") {
    return v === "true";
  }
  return undefined;
}

export function loadDaemonConfig(authDir?: string): DaemonConfig {
  const p = configPath(authDir);
  if (!fs.existsSync(p)) {
    return emptyDaemonConfig();
  }
  try {
    const data = JSON.parse(fs.readFileSync(p, "utf8")) as unknown;
    if (typeof data !== "object" || data === null) {
      return emptyDaemonConfig();
    }
    const o = data as Record<string, unknown>;
    return {
      sync_root: String(o.sync_root || ""),
      connector_instance_id: String(o.connector_instance_id || ""),
      include_subfolders: parseOptionalBool(o.include_subfolders),
    };
  } catch {
    return emptyDaemonConfig();
  }
}

export function saveDaemonConfig(
  cfg: DaemonConfig,
  authDir?: string
): string {
  const store = new FernetFileTokenStore(authDir);
  store.ensureDir();
  const p = configPath(authDir);
  const toWrite: Record<string, string | boolean> = {};
  if (cfg.sync_root) {
    toWrite.sync_root = cfg.sync_root;
  }
  if (cfg.connector_instance_id) {
    toWrite.connector_instance_id = cfg.connector_instance_id;
  }
  if (cfg.include_subfolders !== undefined) {
    toWrite.include_subfolders = cfg.include_subfolders;
  }
  fs.writeFileSync(p, JSON.stringify(toWrite, null, 2), "utf8");
  try {
    fs.chmodSync(p, 0o600);
  } catch {
    /* ignore */
  }
  return p;
}
