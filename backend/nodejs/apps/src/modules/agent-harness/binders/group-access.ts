import type { interfaces } from 'inversify';
import { GroupAccessRouting } from '../application/group-access';
import type { ChatModelGroups, GroupAccess } from '../ports/group-access';
import type { RoutingDecider } from '../ports/routing-decider';
import { boundOrNull } from './context';
import {
  AGENT_HARNESS_CHAT_MODEL_GROUPS,
  AGENT_HARNESS_GROUP_ACCESS,
} from './tokens';

/** The model groups on turns today's loop serves, or undefined where group access is not composed. */
export const chatModelGroupsOf = (
  container: interfaces.Container,
): ChatModelGroups | undefined =>
  boundOrNull<ChatModelGroups>(container, AGENT_HARNESS_CHAT_MODEL_GROUPS) ??
  undefined;

/** The group access an extension bound (G-41), or undefined: nothing is limited by group. */
export const groupAccessOf = (
  container: interfaces.Container,
): GroupAccess | undefined =>
  boundOrNull<GroupAccess>(container, AGENT_HARNESS_GROUP_ACCESS) ?? undefined;

/** `decider` with the harness group (GA-21) on top, where group access is bound. */
export const withGroupAccess = (
  container: interfaces.Container,
  decider: RoutingDecider,
): RoutingDecider => {
  const access = groupAccessOf(container);
  return access === undefined
    ? decider
    : new GroupAccessRouting(decider, access);
};
