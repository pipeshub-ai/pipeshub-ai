/**
 * Container keys of what the admin API (CPD-11) calls, bound by the agent
 * harness's admin binder. A route whose key is unbound answers
 * `CP_UNAVAILABLE`: that part is not composed on this deployment.
 */
export const CONTROL_PLANE_AUTHORIZER = 'ControlPlaneAuthorizer';
export const CONTROL_PLANE_CATALOG = 'ControlPlaneCatalog';
export const CONTROL_PLANE_INSTALLATION_ADMIN = 'ControlPlaneInstallationAdmin';
export const CONTROL_PLANE_GRANT_ADMIN = 'ControlPlaneGrantAdmin';
/** `AdminPinServices`; bound unless `CONTROL_PLANE_PIN=off`. */
export const CONTROL_PLANE_PIN_SERVICES = 'ControlPlanePinServices';
/** A `MembershipPort` (cached), bound where team membership is readable. */
export const CONTROL_PLANE_MEMBERSHIP = 'ControlPlaneMembership';
export const CONTROL_PLANE_POLICY_ADMIN = 'ControlPlanePolicyAdmin';
