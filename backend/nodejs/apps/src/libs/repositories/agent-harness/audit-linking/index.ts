/**
 * The audit link (committed appends onto the org's chain) belongs to the audit chain store that wrote the appends:
 * `audit/index.ts` picks it, and with it this directory's Mongo or PostgreSQL half.
 */
export { createAuditChainStore, auditLinksIdle } from '../audit';
