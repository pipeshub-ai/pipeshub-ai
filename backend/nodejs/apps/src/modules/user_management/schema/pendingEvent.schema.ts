import { Event } from '../services/entity_events.service';
import { Schema, Document } from 'mongoose';

export interface IPendingEvent {
  eventId: string;
  eventType: string;
  payload: Event['payload'];
  timestamp: number;
  status: 'pending' | 'processing' | 'failed';
  retries: number;
  claimedAt?: Date;
  claimToken?: string;
}

export interface PendingEvent extends IPendingEvent, Document {
  eventId: string;
  eventType: string;
  payload: Event['payload'];
  timestamp: number;
  status: 'pending' | 'processing' | 'failed';
  retries: number;
  claimedAt?: Date;
  claimToken?: string;
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
  claimToken: { type: String },
});
