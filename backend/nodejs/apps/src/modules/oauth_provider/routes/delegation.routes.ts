import { Router } from 'express';
import { Container } from 'inversify';
import { NotImplementedError } from '../../../libs/errors/http.errors';

/**
 * Community edition: delegation is an Enterprise capability, and this router
 * is the empty half of the pair.
 *
 * A service account working with its own access is free here — create one,
 * give it narrow rights, mint and revoke its tokens. Letting that account act
 * *as a named person* is the Enterprise half, and this file is where the two
 * editions part company. `config.ts` exports this router for the Community
 * build and the real one from `ee/` for Enterprise, the same way it already
 * switches the feature-flag router.
 *
 * What makes the absence real rather than cosmetic: a delegated token is only
 * ever issued against an existing allow-list entry — TokenExchangeService
 * asks `isDelegationAllowed` before it will mint anything — and an entry can
 * only be created through the routes this file replaces. With no writer, the
 * exchange can never succeed, so the enforcement that stays in the shared
 * code never has anything to let through.
 *
 * The capability probe is deliberately a real answer rather than a 404. The
 * screen that would offer delegation asks this first, so the Community build
 * can say what the capability is and where it lives instead of failing at the
 * moment somebody reaches for it.
 */
export function createDelegationRouter(_container: Container): Router {
  const router = Router();

  router.get('/capability', (_req, res) => {
    res.status(200).json({
      available: false,
      edition: 'community',
      capability: 'service-account-delegation',
    });
  });

  // Everything else answers 501 rather than 404. A missing route reads as a
  // broken deployment; this says the capability is not built into this
  // edition, which is both true and more useful.
  router.use((_req, _res, next) => {
    next(
      new NotImplementedError(
        'Delegation — letting a service account act on behalf of a named ' +
          'person — is an Enterprise capability and is not built into the ' +
          'Community edition. Service accounts acting with their own access ' +
          'are available here.',
      ),
    );
  });

  return router;
}
