import mongoose, { Document, Schema } from 'mongoose';

export interface IEnterpriseSemanticSearch extends Document {
  query: string;
  limit: number;
  isShared: boolean;
  shareLink: string;
  sharedWith: {
    userId: mongoose.Types.ObjectId;
    accessLevel: string;
  }[];
  isArchived: boolean;
  archivedBy: mongoose.Types.ObjectId;
  orgId: mongoose.Types.ObjectId;
  userId: mongoose.Types.ObjectId;
  citationIds: mongoose.Types.ObjectId[]; // Array of references to citation documents
  records: Record<string, any>;
  createdAt: Date;
  updatedAt: Date;
}

const enterpriseSemanticSearchSchema = new Schema<IEnterpriseSemanticSearch>(
  {
    query: { type: String, required: true, index: true },
    limit: { type: Number, required: true },
    orgId: { type: Schema.Types.ObjectId, required: true, index: true },
    userId: { type: Schema.Types.ObjectId, required: true, index: true },
    citationIds: [{ 
      type: Schema.Types.ObjectId, 
      ref: 'citation',
      index: true
    }],
    records: {
      type: Map,
      of: String,
      default: {}
    },
    isShared: { type: Boolean, default: false },
    shareLink: { type: String },
    sharedWith: [
      {
        userId: { type: Schema.Types.ObjectId },
        accessLevel: { type: String, enum: ['read', 'write'], default: 'read' },
      },
      { _id: false },
    ],
    isArchived: { type: Boolean, default: false },
    archivedBy: { type: Schema.Types.ObjectId },
  },
  {
    timestamps: true,
  },
);

enterpriseSemanticSearchSchema.index({ query: 'text' });

const EnterpriseSemanticSearch = mongoose.model<IEnterpriseSemanticSearch>(
  'search',
  enterpriseSemanticSearchSchema,
);

export default EnterpriseSemanticSearch;