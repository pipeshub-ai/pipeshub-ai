/**
 * Visits every screen at phone, tablet and desktop sizes and reports pages that
 * scroll sideways, controls pushed off screen, and uncaught page errors.
 * Run: EVIDENCE_DIR=<dir> npx playwright test --project=responsive
 */
import { runViewportAudit } from './viewport-audit';
import { ROUTES } from './routes';

runViewportAudit(ROUTES);
