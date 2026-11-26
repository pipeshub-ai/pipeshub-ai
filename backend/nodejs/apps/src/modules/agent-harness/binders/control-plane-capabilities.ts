import { envFlag } from '../../../libs/utils/env-flags';
import { activeOrgIds } from '../../../libs/repositories/agent-harness/active-orgs';
import {
  CanonicalEffectiveSetResolver,
  CapabilityMigration,
  CONTROL_PLANE_BINDINGS,
  EffectiveSetShadow,
  HttpLegacyCapabilitySource,
  createCapabilityLedger,
  createGrantStore,
  createInstallationStore,
  parseControlPlanePin,
  prometheusShadowMismatches,
  releaseItemsOf,
  silentControlPlaneLog,
  type BindingService,
  type CapabilityRepair,
  type CapabilitySync,
  type LegacyCapabilitySourceConfig,
  type SnapshotSource,
} from '../../control-plane';
import { canonicalDigest } from '../../../libs/utils/canonical-json';
import { ControlPlaneReviewedAgentAccess } from '../application/control-plane-agent-access';
import { OrgCapabilityRepair } from '../application/org-capability-repair';
import { orgHarnessOn } from '../config';
import type { AgentAccessPolicy } from '../ports/agent-access';
import { boundOrNull, HarnessBinder } from './context';
import { AGENT_HARNESS_PIN_SNAPSHOTS } from './control-plane-pin';
import {
  AGENT_HARNESS_AGENT_ACCESS,
  CONTROL_PLANE_CAPABILITY_REPAIR,
  CONTROL_PLANE_CAPABILITY_SYNC,
} from './tokens';

/** The app's configuration as the host binds it (`'AppConfig'`): where the query service is and the service secrets. */
const APP_CONFIG = 'AppConfig';

/*
 * CPD-7: installations and grants. The agent authority admission asks is
 * wrapped by the control plane's shadow (`CONTROL_PLANE_PIN`, read once
 * here); the sync route's capability migration writes through the
 * control-plane transaction, reading legacy facts from the query service.
 */
export const bindControlPlaneCapabilities: HarnessBinder = ({
  container,
  dependencies,
  controlPlane,
  logger,
}) => {
  const mode = parseControlPlanePin(
    {
      CONTROL_PLANE_PIN: process.env.CONTROL_PLANE_PIN,
    },
    envFlag('AGENT_HARNESS_WORKER_ENABLED'),
  );
  const installations = createInstallationStore();
  const grants = createGrantStore();
  const log = logger ?? silentControlPlaneLog;
  // The shadow compares on the pinner's snapshots; where no pinner is bound (`off`) there is nothing to compare.
  const snapshots = boundOrNull<SnapshotSource>(
    container,
    AGENT_HARNESS_PIN_SNAPSHOTS,
  );
  if (snapshots !== null && !container.isBound(AGENT_HARNESS_AGENT_ACCESS)) {
    const shadow = new EffectiveSetShadow({
      mode,
      snapshots,
      resolver: new CanonicalEffectiveSetResolver(),
      counter: prometheusShadowMismatches,
      log,
    });
    container
      .bind<AgentAccessPolicy>(AGENT_HARNESS_AGENT_ACCESS)
      .toConstantValue(
        new ControlPlaneReviewedAgentAccess(dependencies.agentAccess, shadow),
      );
  }
  if (
    !container.isBound(CONTROL_PLANE_CAPABILITY_SYNC) &&
    container.isBound(APP_CONFIG)
  ) {
    container
      .bind<CapabilitySync>(CONTROL_PLANE_CAPABILITY_SYNC)
      .toDynamicValue(
        (context) =>
          new CapabilityMigration({
            transaction: controlPlane,
            installations,
            grants,
            ledger: createCapabilityLedger(),
            source: new HttpLegacyCapabilitySource(() =>
              context.container.get<LegacyCapabilitySourceConfig>(APP_CONFIG),
            ),
            release: releaseItemsOf,
            digest: canonicalDigest,
            log,
            // Null where the host composes no credential vault: there are then no bindings to revoke.
            credentials: boundOrNull<BindingService>(
              context.container,
              CONTROL_PLANE_BINDINGS,
            ),
            orgs: activeOrgIds,
          }),
      )
      .inSingletonScope();
  }
  if (
    !container.isBound(CONTROL_PLANE_CAPABILITY_REPAIR) &&
    container.isBound(CONTROL_PLANE_CAPABILITY_SYNC)
  ) {
    container
      .bind<OrgCapabilityRepair>(CONTROL_PLANE_CAPABILITY_REPAIR)
      .toDynamicValue(
        (context) =>
          new OrgCapabilityRepair({
            // The sync binding above is the capability migration, which also repairs.
            migration: context.container.get<CapabilitySync & CapabilityRepair>(
              CONTROL_PLANE_CAPABILITY_SYNC,
            ),
            orgs: activeOrgIds,
            harnessOn: (orgId) => orgHarnessOn(orgId),
            ...(logger !== undefined ? { logger } : {}),
          }),
      )
      .inSingletonScope();
  }
};
