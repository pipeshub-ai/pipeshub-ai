import mongoose from 'mongoose';
import { INotification } from '../schema/notification.schema';
import type { NotificationBrokerMessage } from '../utils/notification-payload.resolver';

export interface DeliveryPlan {
  userIds: mongoose.Types.ObjectId[];
  /** User ids whose row is created already read, so they get no pop-up. */
  readFor?: ReadonlySet<string>;
  /** Extra fields for the live push only; never stored. Never throws. */
  pushExtras?: (
    doc: INotification,
  ) => Promise<Record<string, unknown> | undefined>;
}

export interface INotificationDeliveryPolicy {
  /**
   * Decides who gets the event, before the empty-recipients check. Throws
   * `RetryableDeliveryError` when the answer may differ on a later try.
   */
  plan(
    event: NotificationBrokerMessage,
    base: mongoose.Types.ObjectId[],
  ): Promise<DeliveryPlan>;
}

/** A failure that may pass, so the event is retried rather than dropped. */
export class RetryableDeliveryError extends Error {}

export const NOTIFICATION_DELIVERY_POLICY = Symbol.for(
  'INotificationDeliveryPolicy',
);

/** Delivers to the listed recipients. */
export const deliverToRecipients: INotificationDeliveryPolicy = {
  plan: (_event, base) => Promise.resolve({ userIds: base }),
};
