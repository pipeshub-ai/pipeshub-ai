import {
  Router,
  type NextFunction,
  type Request,
  type RequestHandler,
  type Response,
} from 'express';
import { z } from 'zod/v3';
import type { CapabilitySync, OrgReport } from '../../ports/capability-sync';

export const CAPABILITY_SYNC_PATH =
  '/api/v1/control-plane/internal/capabilities/sync';

/** What changed: one agent, or the org's toolset and MCP instances. The org is never in the body. */
const syncRequestSchema = z.union([
  z
    .object({
      agentKey: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/),
    })
    .strict(),
  z.object({ installations: z.literal(true) }).strict(),
]);

interface ServiceRequest extends Request {
  tokenPayload?: { orgId?: unknown };
}

const answer = (report: OrgReport): Record<string, number> => ({
  created: report.created,
  updated: report.updated,
  unchanged: report.unchanged,
  removed: report.removed,
  held: report.holds.length,
});

/**
 * CPD-7's dual write, Node side: the query service, after it wrote an agent
 * or an instance, names what changed; Node reads the facts back through the
 * legacy driver and applies the migration's plan for it. `authorize` is the
 * scoped-token check for `capabilities:sync`; the org is the token's.
 */
export function createCapabilitySyncRouter(deps: {
  readonly authorize: RequestHandler;
  readonly migration: () => CapabilitySync | null;
}): Router {
  const router = Router();
  router.post(
    '/',
    deps.authorize,
    (req: ServiceRequest, res: Response, next: NextFunction) => {
      const orgId = req.tokenPayload?.orgId;
      const body = syncRequestSchema.safeParse(req.body);
      if (typeof orgId !== 'string' || orgId === '') {
        res.status(401).json({
          code: 'CP_FORBIDDEN',
          message: 'The service token names no org',
        });
        return;
      }
      if (!body.success) {
        res.status(400).json({
          code: 'CP_INVALID_CONFIG',
          message: 'Name one agentKey, or installations: true',
        });
        return;
      }
      const migration = deps.migration();
      if (migration === null) {
        res
          .status(503)
          .json({ code: 'CP_UNAVAILABLE', reason: 'capability_sync_unbound' });
        return;
      }
      const run =
        'agentKey' in body.data
          ? migration.syncAgent(orgId, body.data.agentKey)
          : migration.syncInstallations(orgId);
      run
        .then((report) => {
          if (report.failed !== null) {
            res
              .status(503)
              .json({ code: 'CP_UNAVAILABLE', reason: report.failed });
            return;
          }
          res.status(200).json(answer(report));
        })
        .catch(next);
    },
  );
  return router;
}
