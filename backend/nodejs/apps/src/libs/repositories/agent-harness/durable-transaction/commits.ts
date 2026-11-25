import { AfterCommitPublisher } from '../../../utils/after-commit';
import type { Tx } from '../../uow';

/**
 * The transaction handle the harness's stores take (their ports are generic over it): main's backend-neutral unit of
 * work handle. A Mongo store reads the driver session behind it (`mongoSession`), a PostgreSQL store the kernel unit
 * (`kernelTx`); no driver type crosses the ports.
 */
export type StoreSession = Tx;

/**
 * What the harness's transactions announce once committed (LAT1): a store defers a publish with
 * `durableCommits.defer(handle, …)` inside the transaction, keyed by the driver's handle (the Mongo session, the
 * PostgreSQL kernel unit), and the transaction runner flushes it after the commit.
 */
export const durableCommits = new AfterCommitPublisher<object>();
