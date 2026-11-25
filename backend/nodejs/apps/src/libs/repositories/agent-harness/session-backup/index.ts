import type { PgSessionBackup } from './pg';

/** What the backup CLI uses of either store's backup (`MongoSessionBackup`, `PgSessionBackup`). */
export type SessionBackup = Pick<PgSessionBackup, 'export' | 'restore'>;
