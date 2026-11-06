/** Read per call: tests and the replica-set suite toggle it without reloading modules. */
export const isReplicaSet = (): boolean =>
  process.env.REPLICA_SET_AVAILABLE === 'true';
