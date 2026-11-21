import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { getUserRepository } from '../../../libs/repositories/user';
import { getDelegationRepository } from '../../../libs/repositories/delegation';

/**
 * The rules that let a delegated token be issued, re-checked while it is used.
 *
 * A delegated token speaks as a person, so the conditions that justified
 * issuing it have to keep holding for as long as it works — not only at the
 * moment it was signed. Five minutes is plenty of time for a promotion, a
 * switched-off service account or a withdrawn permission to matter.
 *
 * This lives on its own because two middlewares need it and they authenticate
 * different things. `AuthMiddleware` covers `/api` and `/mcp`, and the Python
 * services follow it because they ask `GET /api/v1/users/me/role`.
 * `OAuthAuthMiddleware` covers the OIDC endpoints, where `/oauth2/userinfo`
 * would otherwise keep answering with the person's name and email for the
 * rest of the token's life after the allow-list row behind it was deleted.
 * One copy of the rules, so the two cannot drift.
 *
 * Every failure refuses the token outright rather than downgrading it.
 * Clamping a role would fix Node and leave Python wrong, because Python
 * re-reads the user document instead of trusting the request, and there is no
 * value to clamp that both halves would agree on.
 */
export async function assertDelegationStillHolds(
  actorId: string | { toString(): string },
  subjectUserId: string | { toString(): string },
  orgId: string | { toString(): string },
  subject: { role?: string; kind?: string; isDisabled?: boolean },
): Promise<void> {
  // Checked here as well as by the callers that load the subject for their
  // own reasons, so this function is sufficient on its own.
  if (subject.isDisabled === true) {
    throw new UnauthorizedError('This account is disabled');
  }

  if (subject.role === 'admin') {
    // The subject was promoted after this token was issued. Delegation
    // refuses administrators at mint time precisely so that no machine
    // credential carries admin rights; honouring this token would hand out
    // exactly that, for the rest of its lifetime.
    throw new UnauthorizedError(
      'This token acts for someone who is now an administrator',
    );
  }

  if (subject.kind === 'service') {
    throw new UnauthorizedError('This token acts for a service account');
  }
  if (subject.kind === 'slack') {
    throw new UnauthorizedError('This token acts for a Slack-only account');
  }

  const actor = await getUserRepository().getLiveFields(
    String(orgId),
    String(actorId),
    ['isDisabled'],
    { serviceOnly: true, strict: true },
  );

  // Also closes the race the issuing path cannot: an exchange that read the
  // service account as enabled can still insert its token after the disable
  // has swept the existing ones. Checking on every request means the sweep
  // never had to win.
  if (!actor || actor.isDisabled === true) {
    throw new UnauthorizedError(
      'The service account behind this token is no longer active',
    );
  }

  // And the allow-list entry that authorised this token in the first place.
  // Re-checking the other two rules but not this one would have been the odd
  // omission: an administrator withdrawing a permission is the most
  // deliberate way of saying "stop".
  //
  // It also closes the window the issuing path cannot. The exchange reads the
  // allow-list and then writes a token; a revocation landing between the two
  // produces a token that never had a live row behind it, and revoking rows
  // at that moment cannot catch an insert that has not happened yet.
  const stillAllowed = await getDelegationRepository().isAllowed(
    String(orgId),
    String(actorId),
    String(subjectUserId),
  );

  if (!stillAllowed) {
    throw new UnauthorizedError(
      'This service account may no longer act as this person',
    );
  }
}
