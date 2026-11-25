import {
  CachingAgentDirectory,
  CachingMembershipPort,
  DirectoryEventsSubscriber,
  silentControlPlaneLog,
} from '../../control-plane';
import { HarnessBinder } from './context';
import {
  CONTROL_PLANE_AGENT_DIRECTORY,
  CONTROL_PLANE_DIRECTORY_EVENTS,
  CONTROL_PLANE_MEMBERSHIP,
} from './tokens';

/**
 * Team membership and agent access cached per process (02-lld §8.5),
 * dropped by `teamMembershipChanged` / `agentChanged` on `entity-events`; the
 * subscriber is bound, and started by whoever starts the harness's consumers.
 */
export const bindDirectoryCaches: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  const directory = dependencies.directory;
  if (directory === undefined || container.isBound(CONTROL_PLANE_MEMBERSHIP))
    return;
  const options = { now: () => Date.now() };
  const membership = new CachingMembershipPort(directory.membership, options);
  const agents = new CachingAgentDirectory(directory.agents, options);
  container
    .bind<CachingMembershipPort>(CONTROL_PLANE_MEMBERSHIP)
    .toConstantValue(membership);
  container
    .bind<CachingAgentDirectory>(CONTROL_PLANE_AGENT_DIRECTORY)
    .toConstantValue(agents);
  container
    .bind<DirectoryEventsSubscriber>(CONTROL_PLANE_DIRECTORY_EVENTS)
    .toConstantValue(
      new DirectoryEventsSubscriber(
        directory.events,
        membership,
        agents,
        logger ?? silentControlPlaneLog,
      ),
    );
};
