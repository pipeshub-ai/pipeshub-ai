/**
 * Screenshots of every audited screen at 375px and 1440px, light and dark.
 * Run: E2E_VISUAL=true EVIDENCE_DIR=<dir> [VISUAL_BASELINE_DIR=<dir>] npx playwright test --project=visual
 */
import { runVisualSnapshots } from './visual-snapshots';
import { ROUTES } from './routes';

runVisualSnapshots(ROUTES);
