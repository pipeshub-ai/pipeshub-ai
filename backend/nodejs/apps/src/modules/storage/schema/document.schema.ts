import mongoose, { Schema, Document as MongooseDocument } from 'mongoose';
import {
  ArtifactSet,
  Document as IDocument,
  DocumentPermission,
  StorageInfo,
  DocumentVersion,
  CustomMetadata,
  StorageVendor,
} from '../types/storage.service.types';

// Extend the existing Document interface with Mongoose.Document
// _id comes from MongooseDocument; the plain row type (IDocument) carries it as a string.
export interface DocumentModel
  extends Omit<IDocument, '_id'>, MongooseDocument {}

// Create schema for S3 and AzureBlob (without localPath)
const RemoteStorageInfoSchema = new Schema<StorageInfo>(
  {
    url: { type: String, required: true },
  },
  { _id: false },
);

// Create schema specifically for Local storage (with localPath)
const LocalStorageInfoSchema = new Schema<StorageInfo>(
  {
    url: { type: String, required: true },
    localPath: { type: String },
  },
  { _id: false },
);

// Create schema from CustomMetadata interface
const CustomMetadataSchema = new Schema<CustomMetadata>(
  {
    key: { type: String, required: true },
    value: { type: Schema.Types.Mixed },
  },
  { _id: false },
);

// Create schema from DocumentVersion interface
const DocumentVersionSchema = new Schema<DocumentVersion>(
  {
    version: { type: Number },
    userAssignedVersionLabel: { type: String },
    s3: { type: RemoteStorageInfoSchema },
    azureBlob: { type: RemoteStorageInfoSchema },
    local: { type: LocalStorageInfoSchema },
    note: { type: String },
    extension: { type: String },
    currentVersion: { type: Number },
    createdAt: { type: Number },
    initiatedByUserId: { type: String },
    size: { type: Number },
  },
  { _id: false },
);

const ArtifactSetSchema = new Schema<ArtifactSet>(
  {
    virtualRecordId: { type: String, required: true },
    rev: { type: String, required: true },
    root: { type: String, required: true },
    // Set when a delete claims the revision; presign refuses it from then on.
    retiringAt: { type: Number },
  },
  { _id: false },
);

// Main Document Schema
const DocumentSchema = new Schema(
  {
    orgId: {
      type: mongoose.Types.ObjectId,
      required: true,
    },
    documentName: {
      type: String,
      required: true,
      trim: true,
    },
    alternateDocumentName: {
      type: String,
      trim: true,
    },
    documentPath: {
      type: String,
      trim: true,
      index: true,
    },
    isVersionedFile: {
      type: Boolean,
      required: true,
      default: false,
    },
    mutationCount: { type: Number, default: 1 },
    permissions: {
      type: String,
      enum: [
        'owner',
        'editor',
        'commentator',
        'readonly',
      ] as DocumentPermission[],
    },
    initiatorUserId: {
      type: mongoose.Types.ObjectId,
    },
    sizeInBytes: {
      type: Number,
    },
    mimeType: {
      type: String,
    },
    extension: {
      type: String,
    },
    versionHistory: [DocumentVersionSchema],
    customMetadata: [CustomMetadataSchema],
    currentVersion: {
      type: Number,
      default: 0,
    },
    tags: [
      {
        type: String,
        trim: true,
      },
    ],
    createdAt: {
      type: Number,
      default: () => Date.now(),
    },
    updatedAt: {
      type: Number,
      default: () => Date.now(),
    },
    deletedByUserId: {
      type: String,
    },
    isDeleted: {
      type: Boolean,
      default: false,
    },
    s3: {
      type: RemoteStorageInfoSchema,
    },
    azureBlob: {
      type: RemoteStorageInfoSchema,
    },
    local: {
      type: LocalStorageInfoSchema,
    },
    storageVendor: {
      type: String,
      required: true,
      enum: Object.values(StorageVendor),
    },
    // Set only when the creator sent an Idempotency-Key; see utils/idempotency.ts.
    idempotencyKey: {
      type: String,
    },
    idempotencyFingerprint: {
      type: String,
    },
    // A keyed upload attempt's claim on a document it has not finished storing.
    uploadLeaseToken: {
      type: String,
    },
    uploadLeaseExpiresAt: {
      type: Number,
    },
    // Set when a signed URL is issued for a new document's first file. Only such
    // a document may be aborted, and only once storage confirms the file is absent.
    awaitingDirectUpload: {
      type: Boolean,
    },
    artifactSet: {
      type: ArtifactSetSchema,
    },
    // Server clock (ms) of the retire mark: the document was replaced by a
    // newer stored write and must take no more in-place buffer writes.
    // Downloads and reads are unaffected.
    retiredAt: {
      type: Number,
      default: null,
    },
  },
  {
    timestamps: {
      currentTime: () => Date.now(),
    },
  },
);

DocumentSchema.index({ orgId: 1, _id: 1 });
// Finds a virtual record's record_/metadata_ documents wherever they are filed.
DocumentSchema.index({ orgId: 1, documentName: 1 });
// Lets a retried create find what its first attempt made, and settles two
// attempts racing each other. Keys are per principal (initiatorUserId, null for
// service tokens). Partial, so documents without a key are exempt.
DocumentSchema.index(
  { orgId: 1, initiatorUserId: 1, idempotencyKey: 1 },
  {
    unique: true,
    partialFilterExpression: { idempotencyKey: { $type: 'string' } },
  },
);
// One document per content revision of a virtual record, so two presigns of
// the same revision racing each other settle on one. Its prefix also serves
// listing and purging a virtual record's revisions.
DocumentSchema.index(
  { orgId: 1, 'artifactSet.virtualRecordId': 1, 'artifactSet.rev': 1 },
  {
    unique: true,
    partialFilterExpression: {
      'artifactSet.virtualRecordId': { $exists: true },
    },
  },
);

// Create and export the model
export const DocumentModel = mongoose.model<DocumentModel>(
  'Document',
  DocumentSchema,
);
