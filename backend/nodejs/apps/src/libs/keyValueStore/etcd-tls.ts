import { readFileSync } from 'fs';

/**
 * SEC-D-06: the bundled etcd accepts only TLS clients with a certificate its CA signed (Compose's etcd-tls service
 * writes one per component). ETCD_URL is then https://, and ETCD_CA_FILE, ETCD_CERT_FILE and ETCD_KEY_FILE name
 * this process's files. Python reads the same variables (app/config/providers/etcd/etcd_env.py).
 */
export interface EtcdTlsFiles {
  caFile: string;
  certFile: string;
  keyFile: string;
}

type Env = Record<string, string | undefined>;

export function etcdTlsFromEnv(
  env: Env = process.env,
): EtcdTlsFiles | undefined {
  if (!/^https:\/\//i.test((env.ETCD_URL ?? '').trim())) return undefined;
  const caFile = (env.ETCD_CA_FILE ?? '').trim();
  const certFile = (env.ETCD_CERT_FILE ?? '').trim();
  const keyFile = (env.ETCD_KEY_FILE ?? '').trim();
  const missing = Object.entries({
    ETCD_CA_FILE: caFile,
    ETCD_CERT_FILE: certFile,
    ETCD_KEY_FILE: keyFile,
  })
    .filter(([, value]) => !value)
    .map(([name]) => name);
  if (missing.length > 0) {
    throw new Error(
      `ETCD_URL is https but ${missing.join(', ')} is not set: etcd needs this component's client certificate`,
    );
  }
  return { caFile, certFile, keyFile };
}

/** The etcd3 client's `credentials`, read when the client is built so a renewed certificate is picked up on restart. */
export function etcdCredentials(
  tls: EtcdTlsFiles | undefined,
):
  | { rootCertificate: Buffer; privateKey: Buffer; certChain: Buffer }
  | undefined {
  if (!tls) return undefined;
  return {
    rootCertificate: readFileSync(tls.caFile),
    privateKey: readFileSync(tls.keyFile),
    certChain: readFileSync(tls.certFile),
  };
}
