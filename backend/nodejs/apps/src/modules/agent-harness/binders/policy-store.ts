import type { interfaces } from 'inversify';
import type { PolicyBacking } from '../ports/policy-backing';
import { boundOrNull } from './context';

/** Where an edition's binder puts the policy store its settings write through; unbound, settings keep their KV documents. */
export const AGENT_HARNESS_POLICY_BACKING = 'AgentHarnessPolicyBacking';

export const policyBackingOf = (
  container: interfaces.Container,
): PolicyBacking | undefined =>
  boundOrNull<PolicyBacking>(container, AGENT_HARNESS_POLICY_BACKING) ??
  undefined;

/** `CONTROL_PLANE_POLICY_STORE=on`: the KV policy documents are no longer written, so nothing may read them. */
export const policyStoreIsRecord = (container: interfaces.Container): boolean =>
  policyBackingOf(container)?.mode === 'on';
