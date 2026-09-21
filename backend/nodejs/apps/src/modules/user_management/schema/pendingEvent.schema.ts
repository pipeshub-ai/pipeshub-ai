import { Schema, Document } from 'mongoose';

export interface PendingEvent extends Document {
  eventId: string;
  eventType: string;
  payload: any;
  timestamp: number;
  status: 'pending' | 'processing' | 'failed';
  retries: number;
  claimedAt?: Date;
}

export const pendingEventSchema = new Schema<PendingEvent>({
  eventId: { type: String, required: true },
  eventType: { type: String, required: true },
  payload: { type: Schema.Types.Mixed, required: true },
  timestamp: { type: Number, required: true },
  status: {
    type: String,
    enum: ['pending', 'processing', 'failed'],
    default: 'pending',
  },
  retries: { type: Number, default: 0 },
  claimedAt: { type: Date },
});
