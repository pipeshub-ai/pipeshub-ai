import { randomUUID } from 'node:crypto';
import { hostname } from 'node:os';
import type { Container } from 'inversify';
import type {
  IMessageAdmin,
  IMessageConsumer,
  IMessageProducer,
} from '../../../libs/types/messaging.types';
import {
  BrokerChangeNotifier,
  createHeadRevisions,
  type ChangeNotifier,
} from '../../control-plane';
import { controlPlaneSubscribers } from '../adapters/control-plane/process-changes';
import { createLeaderLease } from '../../../libs/repositories/agent-harness/leader-lease';
import { LeftoverGroupSweeper } from '../application/leftover-group-sweeper';
import { LiveLayerService } from '../application/live-layer-service';
import {
  deploymentRepositoryCap,
  deploymentWebsiteCap,
} from '../domain/website-settings';
import { boundOrNull, HarnessBinder } from './context';
import { policySectionRulesOf } from './control-plane-admin';
import { orgLayers } from './control-plane-pin';

export const AGENT_HARNESS_CHANGE_NOTIFIER = 'AgentHarnessChangeNotifier';
export const AGENT_HARNESS_LIVE_LAYERS = 'AgentHarnessLiveLayers';
export const AGENT_HARNESS_LEFTOVER_GROUPS = 'AgentHarnessLeftoverGroups';

/**
 * The deployment's broker for `control-plane-events`; the consumer is a
 * broadcast subscriber (it sees every event, whatever other processes read).
 * `admin` removes the per-process groups earlier builds left on the broker.
 */
export interface ChangeEventBroker {
  producer: IMessageProducer;
  consumer: IMessageConsumer;
  admin?: Pick<IMessageAdmin, 'removeIdleGroups'>;
}

const RETRY_CAP_MS = 30_000;

/** Doubling from one second to the cap; unref'd, so a broker that never comes back does not hold the process open. */
export const changeListenBackoff = (attempt: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(
      resolve,
      Math.min(1_000 * 2 ** (attempt - 1), RETRY_CAP_MS),
    ).unref();
  });

/**
 * The change notifier and the live layer (CPD-6), whatever `CONTROL_PLANE_PIN`
 * says: control-plane writes happen in every mode, and a worker polls the
 * live route only when it reads pins. Without a broker in the dependencies
 * (tests, a harness-off install) nothing listens; this process's own writes
 * still reach its holders after their commit.
 */
export const bindChangeNotifier: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  const broker = dependencies.changeEvents ?? null;
  if (broker !== null && !container.isBound(AGENT_HARNESS_CHANGE_NOTIFIER)) {
    container
      .bind<BrokerChangeNotifier>(AGENT_HARNESS_CHANGE_NOTIFIER)
      .toConstantValue(
        new BrokerChangeNotifier({
          ...broker,
          subscribers: controlPlaneSubscribers,
          ...(logger !== undefined ? { log: logger } : {}),
        }),
      );
  }
  if (
    broker?.admin !== undefined &&
    !container.isBound(AGENT_HARNESS_LEFTOVER_GROUPS)
  ) {
    container
      .bind<LeftoverGroupSweeper>(AGENT_HARNESS_LEFTOVER_GROUPS)
      .toConstantValue(
        new LeftoverGroupSweeper({
          admin: broker.admin,
          lease: createLeaderLease(),
          holderId: `${hostname()}:${String(process.pid)}:${randomUUID()}`,
          ...(logger !== undefined ? { logger } : {}),
        }),
      );
  }
  if (container.isBound(AGENT_HARNESS_LIVE_LAYERS)) return;
  const layers = orgLayers(container, dependencies);
  const settings = dependencies.rollout.settings?.bind(dependencies.rollout);
  container.bind<LiveLayerService>(AGENT_HARNESS_LIVE_LAYERS).toConstantValue(
    new LiveLayerService({
      heads: createHeadRevisions(),
      ...(layers !== undefined ? { layers } : {}),
      websitesCap: deploymentWebsiteCap(),
      repositoriesCap: deploymentRepositoryCap(),
      carried: policySectionRulesOf(container)
        .filter((rule) => rule.live === true)
        .map((rule) => rule.section),
      ...(settings !== undefined
        ? { killSwitch: async (orgId) => (await settings(orgId)).killSwitch }
        : {}),
    }),
  );
};

/** Starts listening in the background; a broker that is down is retried, and nothing waits for it. */
export function startChangeNotifier(container: Container): void {
  const notifier = boundOrNull<BrokerChangeNotifier>(
    container,
    AGENT_HARNESS_CHANGE_NOTIFIER,
  );
  if (notifier !== null) void notifier.start(changeListenBackoff);
  boundOrNull<LeftoverGroupSweeper>(
    container,
    AGENT_HARNESS_LEFTOVER_GROUPS,
  )?.start();
}

export async function stopChangeNotifier(container: Container): Promise<void> {
  await boundOrNull<LeftoverGroupSweeper>(
    container,
    AGENT_HARNESS_LEFTOVER_GROUPS,
  )?.stop();
  await boundOrNull<BrokerChangeNotifier>(
    container,
    AGENT_HARNESS_CHANGE_NOTIFIER,
  )?.stop();
}

/** The notifier other features subscribe to, when a broker is wired. */
export const changeNotifier = (container: Container): ChangeNotifier | null =>
  boundOrNull<ChangeNotifier>(container, AGENT_HARNESS_CHANGE_NOTIFIER);
