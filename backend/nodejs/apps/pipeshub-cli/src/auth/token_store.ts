import * as crypto from "crypto";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import FernetCtor from "fernet";

const MAGIC = Buffer.from("pipeshub-daemon-auth-v1", "utf8");
const ITERATIONS = 600_000;
const KDF_PASSWORD = "pipeshub-daemon-v1";

/** Match Python `platform.system()` for common OS values. */
export function pythonPlatformSystem(): string {
  switch (process.platform) {
    case "win32":
      return "Windows";
    case "darwin":
      return "Darwin";
    case "linux":
      return "Linux";
    case "freebsd":
      return "FreeBSD";
    case "openbsd":
      return "OpenBSD";
    case "aix":
      return "AIX";
    default: {
      const p = process.platform;
      return p.length ? p[0]!.toUpperCase() + p.slice(1) : p;
    }
  }
}

export type MachineIdentity = {
  hostname?: string;
  system?: string;
  sysPlatform?: string;
  home?: string;
};

/** Same construction as Python `_machine_user_salt` (first 32 bytes of MAGIC|p0|p1|...). */
export function machineUserSalt(identity?: MachineIdentity): Buffer {
  const nodeStr = identity?.hostname ?? os.hostname();
  const systemStr = identity?.system ?? pythonPlatformSystem();
  const sysPlatStr = identity?.sysPlatform ?? process.platform;
  const homeStr =
    identity?.home !== undefined
      ? identity.home
      : process.env.HOME || process.env.USERPROFILE || "";

  const parts: Buffer[] = [
    Buffer.from(nodeStr, "utf8"),
    Buffer.from(systemStr, "utf8"),
    Buffer.from(sysPlatStr, "utf8"),
  ];
  if (homeStr) {
    parts.push(Buffer.from(homeStr, "utf8"));
  }

  const sep = Buffer.from("|", "utf8");
  const chunks: Buffer[] = [];
  for (let i = 0; i < parts.length; i++) {
    if (i > 0) chunks.push(sep);
    chunks.push(parts[i]!);
  }
  const joined = Buffer.concat(chunks);
  return Buffer.concat([MAGIC, joined]).subarray(0, 32);
}

/** Exported for tests (PBKDF2 output must match Python `cryptography`). */
export function deriveKeyForSalt(salt: Buffer): Buffer {
  return crypto.pbkdf2Sync(KDF_PASSWORD, salt, ITERATIONS, 32, "sha256");
}

/** URL-safe base64 key for Fernet (matches Python `base64.urlsafe_b64encode`). */
export function deriveFernetKeyB64(salt: Buffer): string {
  return deriveKeyForSalt(salt).toString("base64url");
}

export function defaultAuthDir(): string {
  if (process.env.PIPESHUB_CONFIG_DIR) {
    return path.resolve(process.env.PIPESHUB_CONFIG_DIR);
  }
  if (process.platform === "win32") {
    const base = process.env.APPDATA || path.join(os.homedir());
    return path.join(base, "Pipeshub");
  }
  const xdg = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  return path.join(xdg, "pipeshub");
}

function createFernet(secretB64: string): {
  encode(plain: string): string;
  decode(token: string): string;
} {
  const fernet = new FernetCtor({ ttl: 0 });
  fernet.setSecret(secretB64);
  const Token = fernet.Token;
  return {
    encode(plain: string): string {
      const token = new Token({ secret: fernet.secret, ttl: 0 });
      return token.encode(plain);
    },
    decode(tok: string): string {
      const token = new Token({ secret: fernet.secret, ttl: 0 });
      return token.decode(tok);
    },
  };
}

/** Fernet-encrypted `auth.enc` on disk (fallback when OS keychain is unavailable). */
export class FernetFileTokenStore {
  static AUTH_FILENAME = "auth.enc";

  private readonly dir: string;
  private readonly filePath: string;

  constructor(authDir?: string) {
    this.dir = authDir ? path.resolve(authDir) : defaultAuthDir();
    this.filePath = path.join(this.dir, FernetFileTokenStore.AUTH_FILENAME);
  }

  get path(): string {
    return this.filePath;
  }

  private fernet(): ReturnType<typeof createFernet> {
    const salt = machineUserSalt();
    const keyB64 = deriveFernetKeyB64(salt);
    return createFernet(keyB64);
  }

  ensureDir(): void {
    fs.mkdirSync(this.dir, { recursive: true, mode: 0o700 });
    try {
      fs.chmodSync(this.dir, 0o700);
    } catch {
      /* ignore */
    }
  }

  load(): Record<string, unknown> | null {
    if (!fs.existsSync(this.filePath)) {
      return null;
    }
    try {
      const raw = fs.readFileSync(this.filePath);
      const dec = this.fernet().decode(raw.toString("utf8").trim());
      return JSON.parse(dec) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  save(data: Record<string, unknown>): void {
    this.ensureDir();
    const plain = JSON.stringify(data);
    const encrypted = this.fernet().encode(plain);
    fs.writeFileSync(this.filePath, encrypted, "utf8");
    try {
      fs.chmodSync(this.filePath, 0o600);
    } catch {
      /* ignore */
    }
  }

  clear(): boolean {
    if (fs.existsSync(this.filePath)) {
      fs.unlinkSync(this.filePath);
      return true;
    }
    return false;
  }
}
