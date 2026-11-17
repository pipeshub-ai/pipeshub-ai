import { createHash } from 'crypto';
import { execFile } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

/**
 * Pushes a folder into the local server's code graph.
 *
 * Written against the documented `/api/v1/local-repos` contract, not against a
 * running server: that API is not in this branch's base yet. Once it lands,
 * run the e2e against a real local server before relying on this.
 *
 * Algorithm: register -> manifest (complete) ->
 * upload what the server lacks in batches -> commit. The server never reads a
 * path from us; paths here are repo-relative.
 */

export interface RepoLimits {
  maxFiles: number;
  maxFileBytes: number;
  maxBatchFiles: number;
  maxBatchBytes: number;
  maxManifestEntries: number;
  maxTotalBytes?: number;
}

export interface RepoStatus {
  repoKey: string;
  name: string;
  state: 'empty' | 'syncing' | 'indexing' | 'ready' | 'failed' | 'deleting';
  files: number;
  indexing?: { total: number; completed: number; failed: number; skipped: number };
}

export class LocalRepoApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export class LocalRepoClient {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => string | null,
    private readonly fetchFn: typeof fetch = fetch,
  ) {}

  register(repoKey: string, name: string) {
    return this.call<{ limits: RepoLimits } & RepoStatus>('PUT', `/${repoKey}`, { name });
  }
  manifest(repoKey: string, body: { files: ManifestEntry[]; complete: boolean; deleted?: string[] }) {
    return this.call<{ upload: string[]; deleted: string[]; unchanged: number; rejected: { path: string; reason: string }[] }>(
      'POST', `/${repoKey}/manifest`, body);
  }
  upload(repoKey: string, files: { path: string; sha256: string; content: string }[]) {
    return this.call<{ stored: string[]; rejected: { path: string; reason: string }[] }>('PUT', `/${repoKey}/files`, { files });
  }
  commit(repoKey: string) {
    return this.call<RepoStatus>('POST', `/${repoKey}/commit`);
  }
  status(repoKey: string) {
    return this.call<RepoStatus>('GET', `/${repoKey}`);
  }
  async list(): Promise<RepoStatus[]> {
    return (await this.call<{ repos: RepoStatus[] }>('GET', '')).repos;
  }
  remove(repoKey: string) {
    return this.call<{ state: string }>('DELETE', `/${repoKey}`);
  }

  private async call<T>(method: string, suffix: string, body?: unknown): Promise<T> {
    const token = this.getToken();
    if (!token) throw new LocalRepoApiError(401, 'Not signed in to the local server.');
    const res = await this.fetchFn(`${this.baseUrl}/api/v1/local-repos${suffix}`, {
      method,
      headers: {
        authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.json().then((j: { detail?: unknown }) => String(j?.detail ?? ''), () => '');
      throw new LocalRepoApiError(res.status, detail || `${method} local-repos failed (${res.status})`);
    }
    return (await res.json()) as T;
  }
}

export interface ManifestEntry {
  path: string;
  sha256: string;
  size: number;
}

interface LoadedFile extends ManifestEntry {
  content: string;
}

const SKIP_DIRS = new Set(['.git', 'node_modules', 'dist', 'build', 'vendor', '.venv', '__pycache__', '.next', 'target']);
/** Never sent, whatever git or .gitignore say: likely secrets (as the CLI's `pipeshub index` skips them). */
const SECRET_FILE = /^(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|dsa|ecdsa|ed25519)(\..*)?|credentials(\..*)?|\.npmrc|\.pypirc|\.netrc)$/i;
/** The server's own limits (local_repo/store.py DEFAULT_LIMITS) are the ceiling; a missing or invalid limit uses it. */
const LIMIT_CEILING = {
  maxFiles: 20000, maxFileBytes: 1024 * 1024, maxBatchFiles: 100, maxBatchBytes: 4 * 1024 * 1024,
  maxManifestEntries: 20000, maxTotalBytes: 512 * 1024 * 1024,
};
type Limits = typeof LIMIT_CEILING;

export function effectiveLimits(server: Partial<Record<keyof Limits, unknown>> | undefined): Limits {
  const out = { ...LIMIT_CEILING };
  for (const k of Object.keys(out) as (keyof Limits)[]) {
    const v = server?.[k];
    if (typeof v === 'number' && Number.isFinite(v) && v > 0) out[k] = Math.min(v, LIMIT_CEILING[k]);
  }
  return out;
}

export interface ListOptions {
  /** Path of git, or null when there is none; default: found on PATH or in the usual install folders. */
  git?: string | null;
}

/** git's path: a packaged app's PATH is minimal (no Homebrew), so the usual install folders are searched too. */
export function findGit(env: NodeJS.ProcessEnv = process.env): string | null {
  const dirs = [...(env.PATH ?? '').split(path.delimiter), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin'];
  for (const dir of dirs) {
    if (!dir) continue;
    const candidate = path.join(dir, process.platform === 'win32' ? 'git.exe' : 'git');
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // next folder
    }
  }
  return null;
}

/** The folder or one above it holds .git: git's view of ignored files is then the only one to trust. */
function insideGitRepo(root: string): boolean {
  for (let dir = path.resolve(root); ; dir = path.dirname(dir)) {
    if (fs.existsSync(path.join(dir, '.git'))) return true;
    if (path.dirname(dir) === dir) return false;
  }
}

function runGit(git: string, root: string): Promise<string[]> {
  // The selected folder's own config must not run anything: no fsmonitor command, no hooks.
  const hooks = fs.mkdtempSync(path.join(os.tmpdir(), 'ph-nohooks-'));
  const args = ['-c', 'core.fsmonitor=', '-c', `core.hooksPath=${hooks}`, 'ls-files', '--cached', '--others', '--exclude-standard', '-z'];
  return new Promise<string[]>((resolve, reject) => {
    execFile(git, args, { cwd: root, maxBuffer: 256 * 1024 * 1024 }, (err, stdout, stderr) => {
      fs.rmSync(hooks, { recursive: true, force: true });
      if (err) reject(new LocalRepoApiError(422, `git could not list the files of ${root}: ${String(stderr || err.message).trim()}. Nothing was sent.`));
      else resolve(stdout.split('\0').filter(Boolean));
    });
  });
}

/** One .gitignore line as a test on a path relative to the folder holding that .gitignore. Negations are dropped (ignores more, never less). */
function ignoreRule(line: string): ((rel: string, isDir: boolean) => boolean) | null {
  let p = line.replace(/\s+$/, '');
  if (!p || p.startsWith('#') || p.startsWith('!')) return null;
  const dirOnly = p.endsWith('/');
  if (dirOnly) p = p.slice(0, -1);
  const anchored = p.includes('/');
  if (p.startsWith('/')) p = p.slice(1);
  const body = p.split('**').map((part) => part.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*').replace(/\?/g, '[^/]')).join('.*');
  const re = new RegExp(anchored ? `^${body}$` : `(^|/)${body}$`);
  return (rel, isDir) => (!dirOnly || isDir) && re.test(rel);
}

async function walkPlainFolder(root: string): Promise<string[]> {
  const out: string[] = [];
  const walk = async (dir: string, rules: { base: string; test: (rel: string, isDir: boolean) => boolean }[]): Promise<void> => {
    const abs = path.join(root, dir);
    const here = [...rules];
    const gi = await fs.promises.readFile(path.join(abs, '.gitignore'), 'utf8').catch(() => '');
    for (const line of gi.split('\n')) {
      const test = ignoreRule(line);
      if (test) here.push({ base: dir, test });
    }
    const ignored = (rel: string, isDir: boolean) =>
      here.some((r) => (r.base === '' || rel.startsWith(`${r.base}/`)) && r.test(r.base ? rel.slice(r.base.length + 1) : rel, isDir));
    for (const entry of await fs.promises.readdir(abs, { withFileTypes: true })) {
      const rel = dir ? `${dir}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name) && !ignored(rel, true)) await walk(rel, here);
      } else if (entry.isFile() && !ignored(rel, false)) out.push(rel);
    }
  };
  await walk('', []);
  return out;
}

/**
 * Tracked plus untracked-not-ignored files of a git working copy. Inside a repository, git must answer: if it is missing or
 * fails for any reason, nothing is listed (a walk that ignores .gitignore would send ignored files such as .env). A folder
 * outside any repository is walked, following its .gitignore files. Likely secrets are dropped either way.
 */
export async function listRepoFiles(root: string, opts: ListOptions = {}): Promise<string[]> {
  const git = opts.git === undefined ? findGit() : opts.git;
  let names: string[];
  if (insideGitRepo(root)) {
    if (!git) throw new LocalRepoApiError(422, `${root} is a git repository, but git was not found. Install git, then add the folder again. Nothing was sent.`);
    names = await runGit(git, root);
  } else {
    names = await walkPlainFolder(root);
  }
  return names.filter((rel) => !SECRET_FILE.test(path.posix.basename(rel)));
}

/** Read failed (permission, locked, vanished): the sync stops rather than let the server count the file as deleted. */
class ReadFailed extends LocalRepoApiError {
  constructor(rel: string, cause: unknown) {
    super(409, `Could not read ${rel} (${(cause as NodeJS.ErrnoException)?.code ?? String(cause)}); nothing was changed. Try again.`);
  }
}

/** A text file within the size limit: a regular file inside the folder, not binary (NUL in the first 4 KiB), valid UTF-8. Null = not indexable. */
async function loadFile(root: string, realRoot: string, rel: string, maxBytes: number): Promise<LoadedFile | null> {
  const normalised = rel.normalize('NFC').split(path.sep).join('/');
  if (Buffer.byteLength(normalised) > 1024) return null;
  const abs = path.resolve(root, rel);
  if (!abs.startsWith(root + path.sep)) return null;
  let handle: fs.promises.FileHandle;
  try {
    // A folder above the file may be a symlink out of the folder; the file itself is opened without following one.
    const parent = await fs.promises.realpath(path.dirname(abs));
    if (parent !== realRoot && !parent.startsWith(realRoot + path.sep)) return null;
    handle = await fs.promises.open(abs, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0));
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ELOOP') return null; // a symlink: not indexed
    throw new ReadFailed(normalised, e);
  }
  try {
    const st = await handle.stat();
    if (!st.isFile() || st.size > maxBytes) return null;
    const bytes = await handle.readFile();
    if (bytes.subarray(0, 4096).includes(0)) return null;
    let content: string;
    try {
      content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      return null; // not UTF-8: not indexable
    }
    return { path: normalised, sha256: createHash('sha256').update(bytes).digest('hex'), size: bytes.length, content };
  } catch (e) {
    throw new ReadFailed(normalised, e);
  } finally {
    await handle.close();
  }
}

export interface IndexResult {
  status: RepoStatus;
  scanned: number;
  uploaded: number;
  skipped: number;
}

export async function indexRepository(
  client: LocalRepoClient,
  root: string,
  repoKey: string,
  name: string,
  opts: ListOptions = {},
): Promise<IndexResult> {
  const registered = await client.register(repoKey, name);
  const limits = effectiveLimits(registered.limits);
  const absRoot = path.resolve(root);
  const realRoot = await fs.promises.realpath(absRoot);
  const names = await listRepoFiles(absRoot, opts);
  const maxFiles = Math.min(limits.maxFiles, limits.maxManifestEntries);
  const loaded: LoadedFile[] = [];
  let total = 0;
  for (const rel of names) {
    const f = await loadFile(absRoot, realRoot, rel, limits.maxFileBytes);
    if (!f) continue;
    loaded.push(f);
    total += f.size;
    // Checked while reading, so a huge folder is refused before it is held in memory.
    if (loaded.length > maxFiles) throw new LocalRepoApiError(413, `This folder has more than ${maxFiles} indexable files; the limit is ${maxFiles}.`);
    if (total > limits.maxTotalBytes) throw new LocalRepoApiError(413, `This folder's indexable files exceed ${limits.maxTotalBytes} bytes, the limit for one repository.`);
  }
  // Complete: every listed file was read (a read failure stopped above), so a path missing here really is gone.
  const plan = await client.manifest(repoKey, {
    files: loaded.map(({ path: p, sha256, size }) => ({ path: p, sha256, size })),
    complete: true,
  });
  const dropped = new Set(plan.rejected.map((r) => r.path));
  const byPath = new Map(loaded.map((f) => [f.path, f]));
  const toSend = plan.upload.filter((p) => byPath.has(p) && !dropped.has(p)).map((p) => byPath.get(p) as LoadedFile);

  let batch: LoadedFile[] = [];
  let batchBytes = 0;
  let uploaded = 0;
  const flush = async (): Promise<void> => {
    if (!batch.length) return;
    const res = await client.upload(repoKey, batch.map(({ path: p, sha256, content }) => ({ path: p, sha256, content })));
    uploaded += res.stored.length;
    batch = [];
    batchBytes = 0;
  };
  for (const f of toSend) {
    if (batch.length >= limits.maxBatchFiles || batchBytes + f.size > limits.maxBatchBytes) await flush();
    batch.push(f);
    batchBytes += f.size;
  }
  await flush();
  const status = await client.commit(repoKey);
  return { status, scanned: names.length, uploaded, skipped: names.length - loaded.length };
}
