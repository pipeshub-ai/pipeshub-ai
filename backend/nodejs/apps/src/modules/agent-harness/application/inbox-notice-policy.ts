import { isPaused, type InboxPreferences } from '../domain/inbox';
import type { InboxPreferenceStore } from '../ports/inbox';
import type { RunEndedNotice, ScheduleNotifier } from '../ports/schedules';

/**
 * A person's Inbox settings applied to their workflows' notices (ADR-INBOX-01 §Mute, §Pause):
 * - a muted workflow's successful runs are not told at all; its failures and its asks still are;
 * - while paused, notices are stored in the Inbox but not pushed, and leave by no other channel;
 * - asks (a run waiting for approval) are never muted or paused.
 * Settings that cannot be read leave the notice as the schedule asked: a person is never silenced by an outage.
 */
export class InboxAwareScheduleNotifier implements ScheduleNotifier {
  constructor(
    private readonly inner: ScheduleNotifier,
    private readonly preferences: Pick<InboxPreferenceStore, 'preferences'>,
    private readonly now: () => number = Date.now,
  ) {}

  approvalWaiting(
    notice: Parameters<ScheduleNotifier['approvalWaiting']>[0],
  ): Promise<void> {
    return this.inner.approvalWaiting(notice);
  }

  async firingRefused(
    notice: Parameters<ScheduleNotifier['firingRefused']>[0],
  ): Promise<void> {
    const prefs = await this.read(notice.orgId, notice.actorId);
    return this.inner.firingRefused(
      prefs !== null && isPaused(prefs, this.now())
        ? { ...notice, quiet: true }
        : notice,
    );
  }

  async runEnded(notice: RunEndedNotice): Promise<void> {
    const prefs = await this.read(notice.orgId, notice.actorId);
    if (prefs === null) return this.inner.runEnded(notice);
    if (
      notice.status === 'completed' &&
      prefs.mutedSchedules.includes(notice.scheduleId)
    )
      return;
    if (!isPaused(prefs, this.now())) return this.inner.runEnded(notice);
    const channels = notice.channels.filter((channel) => channel === 'in_app');
    if (channels.length === 0) return;
    return this.inner.runEnded({ ...notice, channels, quiet: true });
  }

  private async read(
    orgId: string,
    userId: string,
  ): Promise<InboxPreferences | null> {
    try {
      return await this.preferences.preferences(orgId, userId);
    } catch {
      return null;
    }
  }
}
