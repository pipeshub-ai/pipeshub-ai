// Interface for a file record document.
export interface IFileRecordDocument {
  // Optional organization ID
  _key?: string;
  orgId: string;
  // 'name' is required
  name: string;

  // Other file-specific properties
  isFile?: boolean;
  extension?: string | null;
  mimeType?: string | null;
  sizeInBytes?: number;
  webUrl?: string;
  etag?: string | null;
  ctag?: string | null;
  quickXorHash?: string | null;
  crc32Hash?: string | null;
  sha1Hash?: string | null;
  sha256Hash?: string | null;
  externalFileId?: string;
  path?: string;
}
