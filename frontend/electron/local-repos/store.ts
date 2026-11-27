import { randomBytes } from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

export interface LocalRepoEntry {
  repoKey: string;
  name: string;
  rootPath: string;
}

/**
 * rootPath -> repoKey, kept in the app's user-data folder. The key must be
 * stable per working copy (same key = same repo on the server) and is kept out
 * of the user's tree so adding a repository never writes into it.
 */
export class LocalRepoStore {
  private entries: LocalRepoEntry[];

  constructor(private readonly file: string) {
    this.entries = LocalRepoStore.read(file);
  }

  list(): LocalRepoEntry[] {
    return this.entries.map((e) => ({ ...e }));
  }

  findByKey(repoKey: string): LocalRepoEntry | undefined {
    return this.entries.find((e) => e.repoKey === repoKey);
  }

  /** Existing entry for the folder, or a new one with a fresh random key. */
  ensure(rootPath: string): LocalRepoEntry {
    const resolved = path.resolve(rootPath);
    const found = this.entries.find((e) => e.rootPath === resolved);
    if (found) return { ...found };
    const entry = { repoKey: randomBytes(16).toString('hex'), name: path.basename(resolved) || 'repository', rootPath: resolved };
    this.entries.push(entry);
    this.save();
    return { ...entry };
  }

  remove(repoKey: string): void {
    this.entries = this.entries.filter((e) => e.repoKey !== repoKey);
    this.save();
  }

  private save(): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.entries, null, 2));
    fs.renameSync(tmp, this.file);
  }

  private static read(file: string): LocalRepoEntry[] {
    try {
      const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
      return Array.isArray(raw)
        ? raw.filter((e) => typeof e?.repoKey === 'string' && typeof e?.rootPath === 'string' && typeof e?.name === 'string')
        : [];
    } catch {
      return [];
    }
  }
}
