import mongoose, { Document, Schema } from 'mongoose';

interface IBoundingBox {
  x: number;
  y: number;
}

export interface ICitationMetadata {
  blockNum?: number[];
  pageNum?: number[];
  sheetName?: string;
  sheetNum?: number;
  subcategoryLevel1?: string;
  subcategoryLevel2?: string;
  subcategoryLevel3?: string;
  categories?: string;
  departments?: string[];
  connector?: string;
  recordType?: string;
  orgId: string;
  blockType?: string;
  blockText?: string,
  mimeType: string;
  recordId: string;
  chunkIndex: number;
  recordVersion: number;
  topics?: string[];
  languages?: string[];
  bounding_box?: IBoundingBox[];
  recordName: string;
  origin: string;
  extension: string;
  _id?: string;
  _collection_name?: string;
  score?: number;
}

export interface ICitation extends Document {
  content: string;
  chunkIndex: number;
  metadata: ICitationMetadata;
  citationType: string;
  createdAt: Date;
  updatedAt: Date;
}

const boundingBoxSchema = new Schema<IBoundingBox>({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
});

const citationMetadataSchema = new Schema<ICitationMetadata>({
  blockNum: [{ type: Number }],
  pageNum: [{ type: Number }],
  sheetNum: { type: Number },
  sheetName: { type: String },
  subcategoryLevel1: { type: String },
  subcategoryLevel2: { type: String },
  subcategoryLevel3: { type: String },
  categories: { type: String },
  departments: [{ type: String }],
  connector: { type: String },
  recordType: { type: String },
  orgId: { type: String, required: true },
  blockType: { type: String },
  blockText: { type: String },
  mimeType: { type: String, required: true },
  recordId: { type: String, required: true },
  recordVersion: { type: Number, default: 0 },
  topics: [{ type: String }],
  languages: [{ type: String }],
  bounding_box: [boundingBoxSchema],
  recordName: { type: String, required: true },
  origin: { type: String, required: true },
  extension: { type: String, required: true },
  _id: { type: String },
  score: { type: Number },
  _collection_name: { type: String },
});

const citationSchema = new Schema<ICitation>(
  {
    content: { type: String, required: true },
    chunkIndex: { type: Number, required: true },
    metadata: { type: citationMetadataSchema, required: true },
    citationType: { type: String, required: true },
  },
  {
    timestamps: true,
  },
);

citationSchema.statics.createFromAIResponse = async function (
  aiCitation,
  orgId,
) {
  return new this({
    orgId,
    content: aiCitation.content,
    documentIndex: aiCitation.docIndex,
    citationType: aiCitation.citationType,
    citationMetaData: aiCitation.metadata,
  });
};

// Create text index on citation content
citationSchema.index({ content: 'text' });

// Create index on common metadata fields for efficient querying
citationSchema.index({ 'metadata.recordId': 1 });
citationSchema.index({ 'metadata.recordName': 1 });

const Citation = mongoose.model<ICitation>('citations', citationSchema);

export default Citation;
