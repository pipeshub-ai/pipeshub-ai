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
  /**
   * Asked once the rows are stored, before anything is pushed or emailed: which of these user ids may still have
   * the notice. Rows of the others are deleted unseen. The plan's check runs before the insert, so a change that
   * takes someone's access away between the two is seen either here, or, when it commits after this, by that
   * change itself, which then finds the stored row. Throws `RetryableDeliveryError` when it cannot tell.
   */
  stillFor?: (
    userIds: readonly mongoose.Types.ObjectId[],
  ) => Promise<ReadonlySet<string>>;
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
