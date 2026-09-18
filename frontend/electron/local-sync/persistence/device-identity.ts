import { execFile as nodeExecFile } from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as os from 'os';

const LINUX_MACHINE_ID_FILES = ['/etc/machine-id', '/var/lib/dbus/machine-id'];
const IOREG_PATH = '/usr/sbin/ioreg';
/** App startup awaits this lookup, so a wedged child process must not hang the window. */
const EXEC_TIMEOUT_MS = 10_000;

export interface DeviceIdentity {
  deviceId: string;
  deviceName: string;
}

export type ExecFileFn = (file: string, args: string[]) => Promise<string>;
export type ReadFileFn = (filePath: string) => Promise<string>;

export interface DeviceIdentityDeps {
  platform?: NodeJS.Platform;
  execFile?: ExecFileFn;
  readFile?: ReadFileFn;
  hostname?: () => string;
  windir?: string;
}

export class DeviceIdentityError extends Error {
  readonly source: string;

  constructor(source: string, cause: unknown) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    super(`Could not read the machine id from ${source}: ${reason}`, { cause });
    this.name = 'DeviceIdentityError';
    this.source = source;
  }
}

export function parseRegMachineGuid(output: string): string | null {
  const match = /MachineGuid\s+REG_SZ\s+([^\r\n]*)/i.exec(String(output || ''));
  const value = match?.[1]?.trim();
  return value || null;
}

export function parseIoregPlatformUuid(output: string): string | null {
  const match = /"IOPlatformUUID"\s*=\s*"([^"]*)"/.exec(String(output || ''));
  const value = match?.[1]?.trim();
  return value || null;
}

function hashRawId(rawId: string): string {
  return crypto.createHash('sha256').update(rawId.trim().toLowerCase()).digest('hex');
}

function defaultExecFile(file: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    nodeExecFile(
      file,
      args,
      { windowsHide: true, timeout: EXEC_TIMEOUT_MS, encoding: 'utf8' },
      (error, stdout) => {
        if (error) reject(error);
        else resolve(stdout);
      },
    );
  });
}

function defaultReadFile(filePath: string): Promise<string> {
  return fs.promises.readFile(filePath, 'utf8');
}

async function readFromCommand(
  execFile: ExecFileFn,
  file: string,
  args: string[],
  parse: (output: string) => string | null,
  field: string,
): Promise<string> {
  const source = [file, ...args].join(' ');
  let output: string;
  try {
    output = await execFile(file, args);
  } catch (error) {
    throw new DeviceIdentityError(source, error);
  }
  const rawId = parse(output);
  if (!rawId) throw new DeviceIdentityError(source, `${field} not found in output`);
  return rawId;
}

async function readLinuxRawId(readFile: ReadFileFn, hostname: () => string): Promise<string> {
  const failures: string[] = [];
  for (const filePath of LINUX_MACHINE_ID_FILES) {
    try {
      const value = (await readFile(filePath)).trim();
      if (value) return value;
      failures.push(`${filePath} is empty`);
    } catch (error) {
      failures.push(`${filePath}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  const host = String(hostname() || '').trim();
  if (host) return host;
  failures.push('hostname is empty');
  throw new DeviceIdentityError(
    [...LINUX_MACHINE_ID_FILES, 'os.hostname()'].join(', '),
    failures.join('; '),
  );
}

/**
 * The OS machine id, hashed. Survives app reinstalls and is not carried along
 * when app data is copied to another machine, which a stored random id was.
 * There is deliberately no random fallback: a fresh id per launch would hand
 * the connector to a "new" device every time.
 */
export async function resolveDeviceIdentity(deps: DeviceIdentityDeps = {}): Promise<DeviceIdentity> {
  const platform = deps.platform ?? process.platform;
  const execFile = deps.execFile ?? defaultExecFile;
  const readFile = deps.readFile ?? defaultReadFile;
  const hostname = deps.hostname ?? os.hostname;

  let rawId: string;
  switch (platform) {
    case 'win32': {
      const windir = deps.windir ?? (process.env.windir || 'C:\\Windows');
      rawId = await readFromCommand(
        execFile,
        `${windir}\\System32\\reg.exe`,
        ['QUERY', 'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography', '/v', 'MachineGuid'],
        parseRegMachineGuid,
        'MachineGuid',
      );
      break;
    }
    case 'darwin':
      rawId = await readFromCommand(
        execFile,
        IOREG_PATH,
        ['-rd1', '-c', 'IOPlatformExpertDevice'],
        parseIoregPlatformUuid,
        'IOPlatformUUID',
      );
      break;
    case 'linux':
      rawId = await readLinuxRawId(readFile, hostname);
      break;
    default:
      throw new DeviceIdentityError(`platform ${platform}`, 'unsupported platform');
  }

  return { deviceId: hashRawId(rawId), deviceName: hostname() };
}
