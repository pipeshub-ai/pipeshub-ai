import mongoose, { Schema, Types, Document } from 'mongoose';
const ObjectId = mongoose.Schema.Types.ObjectId;

interface IuserDp extends Document {
  userId?: Types.ObjectId;
  orgId?: Types.ObjectId;
  pic?: string | null;
  picUrl?: string | null;
  mimeType?: string | null;
}

const userDisplayPictureSchema = new Schema<IuserDp>({
  userId: {
    type: ObjectId,
    ref: 'users',
  },
  orgId: {
    type: ObjectId,
    ref: 'orgs',
  },
  pic: {
    type: String,
    // Store the logo as a base64 string
  },
  picUrl: {
    type: String,
  },
  mimeType: {
    type: String,
    // Store the MIME type (e.g., 'image/png', 'image/jpeg')
  },
});

export const UserDisplayPicture = mongoose.model<IuserDp>(
  'user-dps',
  userDisplayPictureSchema,
  'user-dps',
);
