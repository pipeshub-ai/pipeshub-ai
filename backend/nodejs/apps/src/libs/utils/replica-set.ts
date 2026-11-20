import { envFlag } from './env-flags';

/** Read per call: tests and the replica-set suite toggle it without reloading modules. */
export const isReplicaSet = (): boolean => envFlag('REPLICA_SET_AVAILABLE');
