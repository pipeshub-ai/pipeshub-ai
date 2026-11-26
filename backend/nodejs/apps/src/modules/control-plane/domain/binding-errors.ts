import { ControlPlaneStoreError } from './store-errors';

/** A migration revert of a binding the migration did not import (a native one is never removed). */
export class BindingNotImported extends ControlPlaneStoreError {
  readonly reason = 'cp_binding_not_imported';
}

/** An OAuth `begin` for an installation kind with no registered start. */
export class OAuthStartUnsupported extends ControlPlaneStoreError {
  readonly reason = 'cp_oauth_start_unsupported';
}
