import { hostname } from 'os';
import {
  createBroadcastMessageConsumer,
  createMessageAdmin,
  createMessageProducerFromConfig,
  resolveMessageBrokerConfig,
} from '../../../../libs/services/message-broker.factory';
import type { Logger } from '../../../../libs/services/logger.service';
import type { AppConfig } from '../../../tokens_manager/config/config';
import type { ChangeEventBroker } from '../../binders/change-notifier';

/**
 * This process's identity as a subscriber: every Node process must see every
 * change event (03-interfaces §5.4). On Kafka it names the process's own
 * consumer group; host and pid are stable across restarts of a container, so
 * a restart reuses its group. Redis Streams holds no group for a subscriber.
 */
export const changeEventsGroupId = (
  host: string = hostname(),
  pid: number = process.pid,
): string => `control-plane-events-${host}-${String(pid)}`;

/** The deployment's broker (`MESSAGE_BROKER`) for `control-plane-events`; nothing connects until it is used. */
export function controlPlaneEventBroker(
  appConfig: AppConfig,
  logger: Logger,
  identity: string = changeEventsGroupId(),
): ChangeEventBroker {
  const resolved = resolveMessageBrokerConfig(appConfig);
  return {
    producer: createMessageProducerFromConfig(appConfig, logger),
    consumer: createBroadcastMessageConsumer(resolved, logger, identity),
    admin: createMessageAdmin(resolved, logger),
  };
}
