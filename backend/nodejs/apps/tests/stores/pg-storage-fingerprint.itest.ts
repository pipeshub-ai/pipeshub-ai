/**
 * The storage fingerprint integration tests (storage-fingerprint.itest.ts) on PostgreSQL and local disk, for the PostgreSQL store
 * suite (`npm run test:pg-stores` picks up pg-*.itest.ts). The document store is chosen for this file only.
 */
process.env.FP_DOC_STORE = 'postgres';
process.env.STORAGE_TYPE = 'local';
// eslint-disable-next-line @typescript-eslint/no-require-imports
require('./storage-fingerprint.itest');
