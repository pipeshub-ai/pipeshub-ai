import { makeOrgContext, runWithOrgContext } from '../../src/libs/context/orgContext'

interface SignedIn {
  orgId?: unknown
  userId?: unknown
}

/**
 * What the auth middleware does once a token verifies: the rest of the request runs in the caller's org context, which is
 * where routes read the org from. A test authenticator that sets `req.user` calls this in place of `next()`.
 */
export function continueAs(user: SignedIn | undefined, next: () => void): void {
  if (typeof user?.orgId !== 'string' || user.orgId === '') {
    next()
    return
  }
  runWithOrgContext(
    makeOrgContext({
      orgId: user.orgId,
      userId: typeof user.userId === 'string' && user.userId !== '' ? user.userId : null,
      kind: 'user',
      source: 'jwt',
    }),
    next,
  )
}

/** A handler called directly, as the request it stands for would run it: in the org context of `req.user`. */
export function asRequestUser<T>(user: SignedIn | undefined, fn: () => T): T {
  let out!: T
  continueAs(user, () => {
    out = fn()
  })
  return out
}

/** Org of a request that names no signed-in org: such a request never reaches a route unauthenticated, so tests give it one. */
export const TEST_REQUEST_ORG = 'org-test'

/**
 * A handler called directly, as the auth middleware would run it: in the org context of `req.user`, or of TEST_REQUEST_ORG when
 * the request names no org.
 */
export function inRequestOrg<H extends (req: any, res: any, next: any) => unknown>(handler: H): H {
  return ((req: any, res: any, next: any) => {
    const user = req?.user
    const signedIn = typeof user?.orgId === 'string' && user.orgId !== '' ? user : { ...(user ?? {}), orgId: TEST_REQUEST_ORG }
    return asRequestUser(signedIn, () => handler(req, res, next))
  }) as H
}

/** A controller whose handler methods are called directly: each runs as `inRequestOrg` would run it. */
export function withRequestOrg<T extends object>(controller: T): T {
  return new Proxy(controller, {
    get(target, key, receiver) {
      const value = Reflect.get(target, key, receiver)
      return typeof value === 'function' ? inRequestOrg((value as (...a: unknown[]) => unknown).bind(target)) : value
    },
  })
}
