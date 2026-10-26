import { createHash } from 'crypto';
import { execFile } from 'child_process';
import * as fs from 'fs';
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

/** Tracked plus untracked-not-ignored files when the folder is a git working copy; else a plain walk. */
export async function listRepoFiles(root: string): Promise<string[]> {
  const viaGit = await new Promise<string[] | null>((resolve) => {
    execFile('git', ['ls-files', '--cached', '--others', '--exclude-standard', '-z'], { cwd: root, maxBuffer: 256 * 1024 * 1024 },
      (err, stdout) => resolve(err ? null : stdout.split('\0').filter(Boolean)));
  });
  if (viaGit) return viaGit;
  const out: string[] = [];
  const walk = async (dir: string): Promise<void> => {
    for (const entry of await fs.promises.readdir(path.join(root, dir), { withFileTypes: true })) {
      const rel = dir ? `${dir}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) await walk(rel);
      } else if (entry.isFile()) out.push(rel);
    }
  };
  await walk('');
  return out;
}

/** Text files within the size limit: not binary (NUL in the first 4 KiB), valid UTF-8, a regular file. */
async function loadFile(root: string, rel: string, maxBytes: number): Promise<LoadedFile | null> {
  const normalised = rel.normalize('NFC').split(path.sep).join('/');
  if (Buffer.byteLength(normalised) > 1024) return null;
  const abs = path.join(root, rel);
  try {
    const st = await fs.promises.lstat(abs);
    if (!st.isFile() || st.size > maxBytes) return null;
    const bytes = await fs.promises.readFile(abs);
    if (bytes.subarray(0, 4096).includes(0)) return null;
    const content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    return { path: normalised, sha256: createHash('sha256').update(bytes).digest('hex'), size: bytes.length, content };
  } catch {
    return null; // vanished, unreadable or not UTF-8: not indexable
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
): Promise<IndexResult> {
  const registered = await client.register(repoKey, name);
  const { limits } = registered;
  const names = await listRepoFiles(root);
  const loaded: LoadedFile[] = [];
  for (const rel of names) {
    const f = await loadFile(root, rel, limits.maxFileBytes);
    if (f) loaded.push(f);
  }
  if (loaded.length > Math.min(limits.maxFiles, limits.maxManifestEntries)) {
    throw new LocalRepoApiError(413, `This folder has ${loaded.length} indexable files; the limit is ${limits.maxFiles}.`);
  }
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
