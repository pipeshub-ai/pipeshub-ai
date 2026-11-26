import type { GroupAccess } from '../ports/group-access';
import type {
  RoutingDecider,
  RoutingDecision,
  RoutingSubject,
} from '../ports/routing-decider';

/**
 * Routing with the harness group (GA-21): a turn whose sender is in none of
 * the groups the org lets use the harness stays on today's loop
 * (`group_not_allowed`), as an org that turned the harness off does. Every
 * admission path that routes asks it: submit, schedules and triggers, the
 * next-run drain and `/me/capabilities`.
 */
export class GroupAccessRouting implements RoutingDecider {
  constructor(
    private readonly inner: RoutingDecider,
    private readonly access: GroupAccess,
  ) {}

  async decide(subject: RoutingSubject): Promise<RoutingDecision> {
    const decision = await this.inner.decide(subject);
    if (!decision.routing.useHarness) return decision;
    if (
      await this.access.admits(subject.orgId, subject.actorId, {
        kind: 'harness',
      })
    ) {
      return decision;
    }
    // The classic switch has nothing left to choose: the turn is on today's loop.
    const { classicRequested: _classic, ...rest } = decision;
    return {
      ...rest,
      routing: {
        ...decision.routing,
        useHarness: false,
        reason: 'group_not_allowed',
      },
    };
  }
}
