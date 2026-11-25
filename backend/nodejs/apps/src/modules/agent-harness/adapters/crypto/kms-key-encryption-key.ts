import { SealedContentError } from '../../domain/errors';
import { DataKeyContext, KeyEncryptionKey } from '../../ports/data-keys';

/**
 * What a managed deployment's KMS must offer (AWS KMS Encrypt/Decrypt, Azure
 * Key Vault wrapKey/unwrapKey and GCP KMS all fit): the KEK never leaves the
 * KMS, and the encryption context is authenticated with the ciphertext.
 */
export interface KmsClient {
  encrypt(request: {
    keyId: string;
    plaintext: Buffer;
    context: Record<string, string>;
  }): Promise<Buffer>;
  decrypt(request: {
    keyId: string;
    ciphertext: Buffer;
    context: Record<string, string>;
  }): Promise<Buffer>;
}

const kmsContext = (context: DataKeyContext): Record<string, string> => ({
  purpose: 'pipeshub-agent-harness/data-key',
  orgId: context.orgId,
  kid: context.kid,
  kv: String(context.version),
});

/** Managed KEK (REPORT 6.16): a seam until a managed profile wires a KMS client. */
export class KmsKeyEncryptionKey implements KeyEncryptionKey {
  readonly id: string;

  constructor(
    private readonly kms: KmsClient,
    private readonly keyId: string,
  ) {
    this.id = `kms:${keyId}`;
  }

  async wrap(dataKey: Buffer, context: DataKeyContext): Promise<string> {
    const wrapped = await this.kms.encrypt({
      keyId: this.keyId,
      plaintext: dataKey,
      context: kmsContext(context),
    });
    return wrapped.toString('base64');
  }

  async unwrap(wrapped: string, context: DataKeyContext): Promise<Buffer> {
    try {
      return await this.kms.decrypt({
        keyId: this.keyId,
        ciphertext: Buffer.from(wrapped, 'base64'),
        context: kmsContext(context),
      });
    } catch {
      throw new SealedContentError(
        'unwrap_failed',
        'The KMS refused to unwrap the data key',
      );
    }
  }
}
