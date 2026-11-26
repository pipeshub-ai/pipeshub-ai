import {
  CachingAgentDirectory,
  CONTROL_PLANE_AUTHORIZER,
  CONTROL_PLANE_PIN_SERVICES,
  controlPlaneAdminBindings,
  type AdminPinServices,
  type PolicySectionRule,
} from '../../control-plane';
import type { interfaces } from 'inversify';
import { boundOrNull, HarnessBinder } from './context';
import { directoryMembership } from './control-plane-pin';
import {
  CONTROL_PLANE_AGENT_DIRECTORY,
  CONTROL_PLANE_POLICY_SECTION_RULES,
} from './tokens';

/**
 * Every list of policy section rules bound to `CONTROL_PLANE_POLICY_SECTION_RULES`, in binding order: each
 * feature that offers a section binds its own list, so they compose. A section two rules offer is refused at boot.
 */
export function policySectionRulesOf(
  container: interfaces.Container,
): readonly PolicySectionRule[] {
  if (!container.isBound(CONTROL_PLANE_POLICY_SECTION_RULES)) return [];
  const rules = container
    .getAll<readonly PolicySectionRule[]>(CONTROL_PLANE_POLICY_SECTION_RULES)
    .flat();
  const seen = new Set<string>();
  for (const rule of rules) {
    if (seen.has(rule.section)) {
      throw new Error(
        `Policy section "${rule.section}" is offered by two rules`,
      );
    }
    seen.add(rule.section);
  }
  return rules;
}

/**
 * CPD-11: the control plane's services (`/api/v1/control-plane`). Binds after
 * the pinner and the directory caches, and takes whichever of them this
 * deployment composed: a part that is absent answers `CP_UNAVAILABLE`.
 */
export const bindControlPlaneAdmin: HarnessBinder = ({
  container,
  dependencies,
  controlPlane,
}) => {
  if (container.isBound(CONTROL_PLANE_AUTHORIZER)) return;
  const membership = directoryMembership(container, dependencies);
  const agents = boundOrNull<CachingAgentDirectory>(
    container,
    CONTROL_PLANE_AGENT_DIRECTORY,
  );
  const pin = boundOrNull<AdminPinServices>(
    container,
    CONTROL_PLANE_PIN_SERVICES,
  );
  for (const [key, value] of controlPlaneAdminBindings({
    transaction: controlPlane,
    ...(membership !== null ? { membership } : {}),
    ...(agents !== null ? { agents } : {}),
    ...(pin !== null ? { pin } : {}),
  })) {
    if (!container.isBound(key)) container.bind(key).toConstantValue(value);
  }
};
