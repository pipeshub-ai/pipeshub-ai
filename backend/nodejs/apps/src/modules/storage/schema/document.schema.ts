import mongoose, { Schema, Document as MongooseDocument } from 'mongoose';
import {
  Document as IDocument,
  DocumentPermission,
  StorageInfo,
  DocumentVersion,
  CustomMetadata,
  StorageVendor,
} from '../types/storage.service.types';

// Extend the existing Document interface with Mongoose.Document
export interface DocumentModel extends IDocument, MongooseDocument {}

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

// Main Document Schema
const DocumentSchema = new Schema(
  {
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
  },
  {
    timestamps: {
      currentTime: () => Date.now(),
    },
  },
);

// Create and export the model
export const DocumentModel = mongoose.model<DocumentModel>('Document', DocumentSchema);
