/**
 * WP-90's cards on a real stack (case list: docs/agent-harness-v2/ui-cases/wp90-admin-ui.json; R19-FU-3).
 *
 *   UI-WHO-1   AI Access › Who agent messages are from: read only, each action type's fixed note in words
 *
 * Shots with UI_CASE_SHOTS: `ui-who-1` (support/case-shots.ts).
 */
import { test, expect } from '../fixtures/api-context.fixture';
import { followSystemTheme, iconFonts, shoot } from './support/case-shots';
import { expectCardsFitAtEveryWidth, settingsCards } from './support/card-fit';
import { expectCardsReadableInBothThemes } from './support/card-contrast';

test.beforeEach(async ({ page }) => {
  await followSystemTheme(page);
  await iconFonts(page);
});

test('UI-WHO-1: AI Access shows who agent messages are from, read only, a note per action type', async ({ page }) => {
  await page.goto('/workspace/ai-access/');
  const heading = page.getByText('Who agent messages are from', { exact: true });
  await expect(heading).toBeVisible({ timeout: 60_000 });
  for (const note of ['Sent via PipesHub agent Release Helper', 'Written via PipesHub agent Release Helper', 'Committed via PipesHub agent Release Helper']) {
    await expect(page.getByText(note).first()).toBeAttached();
  }
  await expectCardsFitAtEveryWidth(page, () => settingsCards(page), 'UI-WHO-1 AI Access cards');
  await expectCardsReadableInBothThemes(page, () => settingsCards(page), 'UI-WHO-1 AI Access cards');
  await shoot(page, 'ui-who-1', () => heading);
});
