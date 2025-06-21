import mongoose from 'mongoose';

/**
 * Enum representing different storage vendors.
 */
export enum StorageVendor {
  S3 = 's3',
  AzureBlob = 'azureBlob',
  Local = 'local',
}

/**
 * Generalized response structure for storage service operations.
 *
 * @template T - The type of data included in the response.
 */
export interface StorageServiceResponse<T> {
  /**
   * The HTTP status code of the operation.
   */
  statusCode: number;

  /**
   * The data resulting from the operation, if successful.
   */
  data?: T;

  /**
   * A message providing additional details about the operation result,
   * typically used for errors or informational purposes.
   */
  msg?: string;
}

/**
 * Represents metadata about a document managed in the storage service.
 */
export interface Document {
  /**
   * The name of the document.
   */
  documentName: string;

  /**
   * An alternative name for the document.
   */
  alternateDocumentName?: string;

  /**
   * The path where the document is stored.
   */
  documentPath?: string;

  /**
   * Indicates whether the document is versioned.
   */
  isVersionedFile: boolean;

  /**
   * Organization Id
   */
  orgId: mongoose.Types.ObjectId;
  /**
   * Mutation count
   */
  mutationCount?: number;
  /**
   * Access permissions for the document.
   */
  permissions?: DocumentPermission;

  /**
   * The ID of the user who created or initiated the document.
   */
  initiatorUserId: mongoose.Types.ObjectId | null;

  /**
   * The size of the document in bytes.
   */
  sizeInBytes?: number;

  /**
   * MIME type of the document (e.g., `application/pdf`).
   */
  mimeType?: string;

  /**
   * The file extension of the document (e.g., `.pdf`, `.docx`).
   */
  extension: string;

  /**
   * Historical versions of the document.
   */
  versionHistory?: DocumentVersion[];

  /**
   * Custom metadata associated with the document.
   */
  customMetadata?: CustomMetadata[];

  /**
   * Number of times the document has been modified.
   */
  currentVersion: number;

  /**
   * Tags associated with the document for categorization.
   */
  tags?: string[];

  /**
   * Timestamp of when the document was created (in milliseconds since epoch).
   */
  createdAt?: number;

  /**
   * Timestamp of the last update to the document (in milliseconds since epoch).
   */
  updatedAt?: number;

  /**
   * ID of the user who deleted the document.
   */
  deletedByUserId?: mongoose.Schema.Types.ObjectId;

  /**
   * Indicates whether the document is deleted.
   */
  isDeleted: boolean;

  /**
   * Information about the document stored in Amazon S3.
   */
  s3?: StorageInfo;

  /**
   * Information about the document stored in Azure Blob Storage.
   */
  azureBlob?: StorageInfo;

  /**
   * Information about the document stored in Local Storage.
   */
  local?: StorageInfo;

  /**
   * Storage vendor
   */
  storageVendor: StorageVendor;
}

/**
 * Defines permissions for document access.
 */
export type DocumentPermission =
  | 'owner'
  | 'editor'
  | 'commentator'
  | 'readonly';

/**
 * Represents a historical version of a document.
 */
export interface DocumentVersion {
  version?: number;
  userAssignedVersionLabel?: string;
  s3?: StorageInfo;
  azureBlob?: StorageInfo;
  local?: StorageInfo;
  note?: string;
  extension?: string;
  currentVersion?: number;
  createdAt?: number;
  mutationCount?: number;
  initiatedByUserId?: mongoose.Schema.Types.ObjectId;
  size?: number;
}

/**
 * Represents custom metadata key-value pairs.
 */
export interface CustomMetadata {
  key: string;
  value: mongoose.Schema.Types.Mixed;
}

/**
 * Information about the storage location of a document.
 */
export interface StorageInfo {
  url: string;
  localPath?: string;
}

/**
 * Payload representing a file to be handled by the storage service.
 */
export interface FilePayload {
  /**
   * The path where the document will be stored in the storage service.
   */
  documentPath: string;

  /**
   * The file data to be stored, represented as a Buffer.
   */
  buffer: Buffer;

  /**
   * The MIME type of the file, e.g., 'application/pdf' or 'image/jpeg'.
   */
  mimeType: string;

  /**
   * Indicates whether the document is versioned.
   */
  isVersioned: boolean;
}
