/** Which store the repositories use. Imports only the shadow check, so every repository index can depend on it. */
import { shadowEnabled, shadowWrap } from './shadow';

export type DocStoreKind = 'mongo' | 'postgres';

/**
 * `DOC_STORE`: where the application's documents live. Mongo unless an install says otherwise. `shadow` is Mongo with sampled
 * comparison reads on PostgreSQL (shadow.ts): everything that asks which store is live is told `mongo`.
 */
export function docStore(): DocStoreKind {
  const v = (process.env.DOC_STORE ?? '').trim().toLowerCase() || 'mongo';
  if (v === 'mongo' || v === 'postgres') return v;
  if (v === 'shadow') return 'mongo';
  throw new Error(
    `DOC_STORE must be 'mongo', 'postgres' or 'shadow', got '${v}'`,
  );
}

export class DocStoreNotReady extends Error {
  constructor(name: string) {
    super(
      `DOC_STORE=postgres, but the ${name} repository has no PostgreSQL implementation yet`,
    );
  }
}

/** Repositories that fell back to Mongo under `DOC_STORE=postgres` (see `DOC_STORE_MONGO_REPOS`), for the boot log. */
export const mongoFallbacks = new Set<string>();

/**
 * Testing aid, while repositories are ported one by one: `DOC_STORE_MONGO_REPOS` lists repositories (directory names under
 * `libs/repositories`, comma separated) that stay on Mongo under `DOC_STORE=postgres`; `*` means every repository that has
 * no PostgreSQL implementation yet. Unset (the default), a repository without one refuses to start.
 */
export function fallbackList(): Set<string> {
  return new Set(
    (process.env.DOC_STORE_MONGO_REPOS ?? '')
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean),
  );
}

/** Whether this process needs a MongoDB connection: always on Mongo, under PostgreSQL only while a repository is kept there. */
export function usesMongo(): boolean {
  return docStore() === 'mongo' || fallbackList().size > 0;
}

/**
 * The repository for the selected store. A repository without a PostgreSQL implementation refuses to start under
 * `DOC_STORE=postgres` rather than silently reading Mongo, unless `DOC_STORE_MONGO_REPOS` names it.
 */
export function pickStore<T>(
  name: string,
  impls: { mongo: () => T; postgres?: () => T },
): T {
  if (shadowEnabled())
    return impls.postgres
      ? (shadowWrap(
          name,
          impls.mongo() as object,
          impls.postgres as () => object,
        ) as T)
      : impls.mongo();
  if (docStore() === 'mongo') return impls.mongo();
  const fallback = fallbackList();
  const onMongo = fallback.has(name) || (!impls.postgres && fallback.has('*'));
  if (!onMongo) {
    if (!impls.postgres) throw new DocStoreNotReady(name);
    return impls.postgres();
  }
  mongoFallbacks.add(name);
  return impls.mongo();
}
