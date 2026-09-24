import * as fs from 'fs';

/**
 * Node's TLS `ca` option takes certificate *contents*, not a path — handing
 * it `/etc/ssl/redis-ca.pem` makes it parse that string as a PEM, find no
 * certificate in it, and silently fall back to the default trust store. A
 * MemoryDB endpoint fronted by a private CA then fails verification for a
 * reason nothing in the logs points at. (redis-py's `ssl_ca_certs` does take
 * a path, which is why only the Node side needs this.)
 *
 * Read once at provider construction so a missing or unreadable file fails
 * loudly at startup rather than on the first reconnect.
 */
export function readTlsCaCertificate(
  caPath: string | undefined,
): Buffer | undefined {
  if (!caPath) {
    return undefined;
  }
  try {
    return fs.readFileSync(caPath);
  } catch (error) {
    throw new Error(
      `REDIS_TLS_CA_PATH='${caPath}' could not be read: ` +
        `${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
