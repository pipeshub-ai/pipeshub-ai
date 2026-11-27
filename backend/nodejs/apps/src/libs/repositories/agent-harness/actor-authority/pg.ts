import { oidOf } from '../../pg-util';
import { getUserRepository } from '../../user';
import { inBoundOrgUnit } from '../pg-documents/ambient';
import { type ActorReader, HarnessActorAuthority } from './mongo';

/** The actor's account through main's user repository: in the org named, removed or not (the checks then refuse it). */
export const repositoryActorReader: ActorReader = async (orgId, actorId) => {
  const user = await getUserRepository().getAny(orgId, actorId);
  return user === null
    ? null
    : {
        orgId: user.orgId,
        email: user.email,
        ...(user.fullName !== undefined ? { fullName: user.fullName } : {}),
        ...(user.role !== undefined ? { role: user.role } : {}),
        ...(user.kind !== undefined ? { kind: user.kind } : {}),
        ...(user.isDisabled !== undefined
          ? { isDisabled: user.isDisabled }
          : {}),
        ...(user.isDeleted !== undefined ? { isDeleted: user.isDeleted } : {}),
      };
};

/**
 * The schedule actor authority (P8a) with the account read through main's user repository (any document store). The
 * firer has no organisation bound, and main's repositories run as the bound one: each check binds the org it is for.
 */
export class PgActorAuthority extends HarnessActorAuthority {
  constructor(jwtSecret: () => string) {
    super(jwtSecret, repositoryActorReader);
  }

  override authorize(
    orgId: string,
    actorId: string,
    options?: { ttlS?: number },
  ): ReturnType<HarnessActorAuthority['authorize']> {
    // An id that is no org's is refused before any read, with nothing to bind.
    if (oidOf(orgId) === undefined)
      return super.authorize(orgId, actorId, options);
    return inBoundOrgUnit(orgId, () =>
      super.authorize(orgId, actorId, options),
    );
  }

  override isOrgAdmin(orgId: string, userId: string): Promise<boolean> {
    if (oidOf(orgId) === undefined) return super.isOrgAdmin(orgId, userId);
    return inBoundOrgUnit(orgId, () => super.isOrgAdmin(orgId, userId));
  }
}
