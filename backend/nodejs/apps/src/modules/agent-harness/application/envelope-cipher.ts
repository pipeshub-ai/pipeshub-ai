import {
  openBlob,
  openContent,
  SealContext,
  SealedContent,
  sealedBlobKeyRef,
  sealedKeyRef,
  SealPurpose,
  sealBlob,
  sealContent,
} from '../domain/data-protection';
import { OrgDataKeys } from './org-data-keys';

/**
 * Seals and opens org content with the org's data keys. Inline form for
 * event data (stored in the event document), binary form for claim-checked
 * payloads; the P3 CAS and durable checkpoints are meant to reuse `sealBlob`
 * with their own purpose.
 */
export class EnvelopeCipher {
  constructor(private readonly keys: OrgDataKeys) {}

  async seal(
    orgId: string,
    purpose: SealPurpose,
    context: SealContext,
    plaintext: Buffer,
  ): Promise<SealedContent> {
    return sealContent(
      await this.keys.active(orgId),
      purpose,
      context,
      plaintext,
    );
  }

  async open(
    orgId: string,
    purpose: SealPurpose,
    context: SealContext,
    sealed: SealedContent,
  ): Promise<Buffer> {
    const key = await this.keys.byRef(orgId, sealedKeyRef(sealed));
    return openContent(key, purpose, context, sealed);
  }

  async sealBlob(
    orgId: string,
    purpose: SealPurpose,
    context: SealContext,
    plaintext: Buffer,
  ): Promise<Buffer> {
    return sealBlob(await this.keys.active(orgId), purpose, context, plaintext);
  }

  async openBlob(
    orgId: string,
    purpose: SealPurpose,
    context: SealContext,
    bytes: Buffer,
  ): Promise<Buffer> {
    const key = await this.keys.byRef(orgId, sealedBlobKeyRef(bytes));
    return openBlob(key, purpose, context, bytes);
  }
}
