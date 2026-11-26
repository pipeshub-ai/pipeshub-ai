import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import { RELEASE_SNAPSHOT_TTL_DAYS } from '../domain/snapshot-ttl';
import type { SnapshotTtlSource } from '../ports/sandbox-snapshots';
import type { TriggerSettings } from '../ports/schedules';

/** How long every org's sandbox snapshots are kept: the release's period. */
export const RELEASE_SNAPSHOT_TTL: SnapshotTtlSource = {
  days: () => Promise.resolve(RELEASE_SNAPSHOT_TTL_DAYS),
};

/** Every org's webhook limits and completion-notice channels: the release's. */
export const RELEASE_TRIGGER_SETTINGS: TriggerSettings = {
  webhookMaxBytes: () =>
    Promise.resolve(RELEASE_DEFAULTS.triggers.webhookMaxBytes),
  webhookPerMinute: () =>
    Promise.resolve(RELEASE_DEFAULTS.triggers.webhookPerMinute),
  notifyChannels: () =>
    Promise.resolve([...RELEASE_DEFAULTS.triggers.notifyChannels]),
};
