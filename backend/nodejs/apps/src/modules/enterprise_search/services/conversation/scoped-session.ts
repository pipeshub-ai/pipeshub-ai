import { Types } from 'mongoose';
import { ConversationAccessFields } from './domain/types';

/** Identity and state fields a guard stores on its grant. */
export interface ScopedSession extends ConversationAccessFields {
  readonly _id: Types.ObjectId;
  readonly initiator?: { toString(): string };
  readonly isArchived?: boolean;
  readonly agentKey?: string;
}
