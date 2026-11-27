import {
  DeploymentApprovalRules,
  type ApprovalRulesReader,
} from '../application/deployment-approval-rules';
import {
  noEngineCeilings,
  type EngineProfileCeilings,
} from '../ports/engine-profiles';
import type { HarnessBinder } from './context';
import { AdminListNames } from '../application/admin-list-names';
import {
  AGENT_HARNESS_ADMIN_LIST_NAMES,
  AGENT_HARNESS_APPROVAL_SETTINGS,
  AGENT_HARNESS_ENGINE_PROFILES,
} from './tokens';

/** Engine profile ceilings (E4a): an extension's, bound before the built-in binders; else no org ceiling. */
export const bindEngineProfiles: HarnessBinder = ({ container }) => {
  if (container.isBound(AGENT_HARNESS_ENGINE_PROFILES)) return;
  container
    .bind<EngineProfileCeilings>(AGENT_HARNESS_ENGINE_PROFILES)
    .toConstantValue(noEngineCeilings);
};

export const bindApprovalSettings: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  const approvalSettings = dependencies.approvalSettings;
  if (!container.isBound(AGENT_HARNESS_ADMIN_LIST_NAMES)) {
    const groupNames = dependencies.groupMembership?.groupNames?.bind(
      dependencies.groupMembership,
    );
    container
      .bind<AdminListNames>(AGENT_HARNESS_ADMIN_LIST_NAMES)
      .toConstantValue(
        new AdminListNames(
          {
            agents: dependencies.auditNames?.agents ?? null,
            groups: groupNames !== undefined ? { groupNames } : null,
          },
          logger,
        ),
      );
  }
  if (
    approvalSettings === undefined ||
    container.isBound(AGENT_HARNESS_APPROVAL_SETTINGS)
  ) {
    return;
  }
  container
    .bind<ApprovalRulesReader>(AGENT_HARNESS_APPROVAL_SETTINGS)
    .toConstantValue(new DeploymentApprovalRules(approvalSettings.document));
};
