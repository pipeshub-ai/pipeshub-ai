/**
 * AI settings' cards on a real stack (R19-FU-3, R26-DARK CARDS-READABLE-BOTH-THEMES).
 *
 *   UI-AIS-1   AI settings › Coding engine: the org's default engine and the long-work switch, every card fits
 *              its width and reads in both themes
 *
 * UI-WHO-1 (Who agent messages are from) left with its card in b302c8b6a3: this page shows only the cards
 * this repository ships.
 *
 * Shots with UI_CASE_SHOTS: `ui-ais-1` (support/case-shots.ts).
 */
import { test, expect } from '../fixtures/api-context.fixture';
import { followSystemTheme, iconFonts, shoot } from './support/case-shots';
import { expectCardsFitAtEveryWidth, settingsCards } from './support/card-fit';
import { expectCardsReadableInBothThemes } from './support/card-contrast';

test.beforeEach(async ({ page }) => {
  await followSystemTheme(page);
  await iconFonts(page);
});

test('UI-AIS-1: AI settings shows the coding engine card; every card fits and reads in both themes', async ({ page }) => {
  await page.goto('/workspace/ai-access/');
  const heading = page.getByText('Coding engine', { exact: true });
  await expect(heading).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Default coding engine for the org', { exact: true })).toBeVisible();
  await expect(page.getByText('Hand long work to the coding engine', { exact: true })).toBeVisible();
  await expect(settingsCards(page).first()).toBeVisible();
  await expectCardsFitAtEveryWidth(page, () => settingsCards(page), 'UI-AIS-1 AI settings cards');
  await expectCardsReadableInBothThemes(page, () => settingsCards(page), 'UI-AIS-1 AI settings cards');
  await shoot(page, 'ui-ais-1', () => heading);
});
