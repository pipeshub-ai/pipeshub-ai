/**
 * Citation viewing without re-streaming the record, end to end.
 *
 * Only the model's answer is faked (its stream route returns a fixed answer with citations to real,
 * uploaded records). Everything the viewer does with those records is the real server's: the details
 * call, the record stream, conversions, validators. `helpers/record-stream.helper.ts` counts what the
 * browser asked for and what the server converted; a test that cannot count fails.
 */
import { test, expect } from '../fixtures/api-context.fixture';
import { request, type APIRequestContext } from '@playwright/test';
import { loginViaUI } from '../helpers/login.helper';
import { ensureSmtpConfigured } from '../helpers/smtp.helper';
import { acceptInvite, deleteUserByEmail, uniqueMemberEmail } from '../helpers/members.helper';
import {
  MIME,
  countConversions,
  makeDocx,
  makePdf,
  makePptx,
  makeXlsx,
  recordStreamLog,
} from '../helpers/record-stream.helper';
import {
  askCitedQuestion,
  holdRenderer,
  isAttached,
  isInView,
  jumpTo,
  logoutViaUi,
  rendererNode,
  scrollViewer,
  visiblePdfPage,
  type Cite,
  type CitedRecord,
} from '../helpers/cited-chat.helper';
import {
  createTestKb,
  deleteTestKb,
  makeKbName,
  uploadFileByApi,
  waitForIndexed,
} from '../knowledge-base/kb-upload.helpers';

const MIB = 1024 * 1024;

async function upload(kbId: string, name: string, mimeType: string, buffer: Buffer): Promise<CitedRecord> {
  const id = await uploadFileByApi(kbId, { name, mimeType, buffer });
  return { id, name, mimeType, extension: name.split('.').pop() ?? '' };
}

function textDoc(label: string, stamp: string): Buffer {
  return Buffer.from(`Citation viewer ${label} ${stamp}.\n\n${'The cited passage sits here. '.repeat(40)}\n`);
}

test.describe('Citation viewer cache', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(300_000);

  const stamp = Date.now().toString(36);
  let kbId: string;

  test.beforeAll(async ({ apiContext }) => {
    kbId = (await createTestKb(apiContext, makeKbName('viewer-cache'))).id;
  });

  test.afterAll(async ({ apiContext }) => {
    if (kbId) await deleteTestKb(apiContext, kbId);
  });

  // ── One body across many jumps ─────────────────────────────────────────

  test('ten jumps stream and convert the record once @smoke', async ({ page }) => {
    const pdf = await upload(kbId, `jumps-${stamp}.pdf`, MIME.pdf, makePdf(12, 8 * MIB));
    const cites: Cite[] = Array.from({ length: 10 }, (_, i) => ({
      record: pdf,
      claim: `Claim ${i + 1} is on page ${i + 1}`,
      content: `Passage on page ${i + 1}`,
      pageNum: [i + 1],
    }));
    const log = recordStreamLog(page);
    await askCitedQuestion(page, cites);

    await jumpTo(page, 1);
    const handle = await holdRenderer(page);
    await expect.poll(() => visiblePdfPage(page), { timeout: 60_000 }).toBe(1);

    for (const n of [4, 7, 2]) {
      await jumpTo(page, n);
      await expect.poll(() => visiblePdfPage(page), { timeout: 15_000 }).toBe(n);
    }
    // The same chip twice, after scrolling away.
    await jumpTo(page, 5);
    await expect.poll(() => visiblePdfPage(page)).toBe(5);
    await scrollViewer(page, 'bottom');
    await jumpTo(page, 5);
    await expect.poll(() => visiblePdfPage(page), { timeout: 15_000 }).toBe(5);
    // The preview's own citation cards.
    for (const n of [9, 3]) {
      await page.getByText(`Passage on page ${n}`, { exact: true }).last().click();
      await expect.poll(() => visiblePdfPage(page), { timeout: 15_000 }).toBe(n);
    }

    await log.settled();
    expect(log.bodies(pdf.id), 'the record should be streamed once').toHaveLength(1);
    expect(await isAttached(handle), "the renderer's document node should never be replaced").toBe(true);
  });

  test('a chip clicked while a presentation converts lands on the last chip, with one conversion', async ({ page }) => {
    const deck = await upload(kbId, `deck-${stamp}.pptx`, MIME.pptx, await makePptx(10));
    const cites: Cite[] = Array.from({ length: 10 }, (_, i) => ({
      record: deck,
      claim: `Slide claim ${i + 1}`,
      content: `SLIDE-${i + 1}`,
      pageNum: [i + 1],
    }));
    const log = recordStreamLog(page);
    await askCitedQuestion(page, cites);
    const since = new Date();

    // Chip 2 while chip 1's load is running: no second load, and the viewer ends on slide 2.
    await jumpTo(page, 1);
    await jumpTo(page, 2);
    await holdRenderer(page);
    await expect.poll(() => visiblePdfPage(page), { timeout: 120_000 }).toBe(2);

    for (let n = 3; n <= 10; n++) await jumpTo(page, n);
    await expect.poll(() => visiblePdfPage(page), { timeout: 15_000 }).toBe(10);

    await log.settled();
    expect(log.bodies(deck.id), 'the converted record should be streamed once').toHaveLength(1);
    expect(log.forRecord(deck.id).every((c) => c.convertTo === 'application/pdf')).toBe(true);
    expect(countConversions(since), 'the server should convert once').toBe(1);
  });

  test('spreadsheet jumps stream once and land on the cited rows', async ({ page }) => {
    const sheet = await upload(kbId, `rows-${stamp}.xlsx`, MIME.xlsx, makeXlsx(200));
    const rows = [3, 60, 120, 190];
    const cites: Cite[] = rows.map((r) => ({ record: sheet, claim: `Row claim ${r}`, content: `ROW-${r}`, blockNum: [r] }));
    const log = recordStreamLog(page);
    await askCitedQuestion(page, cites);
    for (let i = 0; i < rows.length; i++) {
      await jumpTo(page, i + 1);
      await expect(rendererNode(page).locator(`tr[data-row="${rows[i] + 1}"], tr[data-row="${rows[i]}"]`).first()).toBeVisible({ timeout: 60_000 });
    }
    await log.settled();
    expect(log.bodies(sheet.id)).toHaveLength(1);
  });

  test('the same chip twice re-scrolls in docx and xlsx', async ({ page }) => {
    const docx = await upload(kbId, `paras-${stamp}.docx`, MIME.docx, await makeDocx(120));
    const sheet = await upload(kbId, `again-${stamp}.xlsx`, MIME.xlsx, makeXlsx(300));
    const log = recordStreamLog(page);

    await askCitedQuestion(page, [
      { record: docx, claim: 'Docx claim', content: 'PARA-80', blockNum: [80] },
    ]);
    await jumpTo(page, 1);
    const para = rendererNode(page).getByText('PARA-80', { exact: false }).first();
    await expect(para).toBeVisible({ timeout: 60_000 });
    await scrollViewer(page, 'top');
    await expect.poll(() => isInView(para), { timeout: 10_000 }).toBe(false);
    const before = log.calls.length;
    await jumpTo(page, 1);
    await expect.poll(() => isInView(para), { timeout: 15_000 }).toBe(true);
    expect(log.calls.length, 'no new request for the same chip').toBe(before);

    await askCitedQuestion(page, [
      { record: sheet, claim: 'Sheet claim', content: 'ROW-250', blockNum: [250] },
    ]);
    await jumpTo(page, 1);
    const row = rendererNode(page).locator('tr[data-row="250"], tr[data-row="251"]').first();
    await expect(row).toBeVisible({ timeout: 60_000 });
    await scrollViewer(page, 'top');
    await expect.poll(() => isInView(row), { timeout: 10_000 }).toBe(false);
    const beforeSheet = log.calls.length;
    await jumpTo(page, 1);
    await expect.poll(() => isInView(row), { timeout: 15_000 }).toBe(true);
    expect(log.calls.length).toBe(beforeSheet);
  });

  // ── Re-opens: validators ───────────────────────────────────────────────

  test('reopening an unchanged record revalidates with 304', async ({ page }) => {
    const a = await upload(kbId, `reopen-a-${stamp}.txt`, MIME.txt, textDoc('A', stamp));
    const b = await upload(kbId, `reopen-b-${stamp}.txt`, MIME.txt, textDoc('B', stamp));
    const log = recordStreamLog(page);
    await askCitedQuestion(page, [
      { record: a, claim: 'Claim in A', content: 'The cited passage sits here.' },
      { record: b, claim: 'Claim in B', content: 'The cited passage sits here.' },
    ]);
    const since = new Date();
    await jumpTo(page, 1);
    await expect(rendererNode(page)).toBeVisible({ timeout: 60_000 });
    await jumpTo(page, 2);
    await expect.poll(async () => { await log.settled(); return log.forRecord(b.id).length; }, { timeout: 30_000 }).toBeGreaterThan(0);
    log.reset();
    await jumpTo(page, 1);
    await expect.poll(async () => { await log.settled(); return log.forRecord(a.id).length; }, { timeout: 30_000 }).toBe(1);

    const [call] = log.forRecord(a.id);
    expect(call.ifNoneMatch, 'the re-open should carry a validator').toBeTruthy();
    expect(call.status).toBe(304);
    expect(call.bytes).toBe(0);
    expect(countConversions(since)).toBe(0);
  });

  test('a re-index keeps the 304', async ({ page, apiContext }) => {
    const a = await upload(kbId, `reindex-${stamp}.txt`, MIME.txt, textDoc('reindex', stamp));
    await waitForIndexed(apiContext, a.id);
    const other = await upload(kbId, `reindex-other-${stamp}.txt`, MIME.txt, textDoc('other', stamp));
    const log = recordStreamLog(page);
    await askCitedQuestion(page, [
      { record: a, claim: 'Claim reindex', content: 'The cited passage sits here.' },
      { record: other, claim: 'Claim other', content: 'The cited passage sits here.' },
    ]);
    await jumpTo(page, 1);
    await expect(rendererNode(page)).toBeVisible({ timeout: 60_000 });
    await log.settled();
    const first = log.forRecord(a.id)[0];
    expect(first.status).toBe(200);
    expect(first.etag, 'a 200 should carry an ETag').toBeTruthy();
    expect(first.cacheControl ?? '').toContain('private');
    expect(first.cacheControl ?? '').toContain('no-store');
    await jumpTo(page, 2);
    await expect(page.getByText(other.name).first()).toBeVisible({ timeout: 30_000 });

    const reindex = await apiContext.post(`/api/v1/knowledgeBase/reindex/record/${a.id}`);
    expect(reindex.ok(), `reindex answered ${reindex.status()}`).toBe(true);
    await waitForIndexed(apiContext, a.id);

    log.reset();
    await jumpTo(page, 1);
    await expect.poll(async () => { await log.settled(); return log.forRecord(a.id).length; }, { timeout: 30_000 }).toBe(1);
    const [call] = log.forRecord(a.id);
    expect(call.status, 'unchanged bytes after a re-index should still answer 304').toBe(304);
    expect(call.bytes).toBe(0);
  });

  test('logout clears the record cache', async ({ browser, apiContext }) => {
    const a = await upload(kbId, `logout-${stamp}.txt`, MIME.txt, textDoc('logout', stamp));
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;
    expect(email && password, 'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set').toBeTruthy();
    const orgName = await orgNameOf(apiContext);

    // A session of its own, so signing out does not end the session the other tests share.
    const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    try {
      const page = await context.newPage();
      await loginViaUI(page, email!, password!);
      const log = recordStreamLog(page);
      await askCitedQuestion(page, [{ record: a, claim: 'Claim logout', content: 'The cited passage sits here.' }]);
      await jumpTo(page, 1);
      await expect(rendererNode(page)).toBeVisible({ timeout: 60_000 });
      await log.settled();
      expect(log.bodies(a.id)).toHaveLength(1);

      await logoutViaUi(page, orgName);
      await loginViaUI(page, email!, password!);
      log.reset();
      await askCitedQuestion(page, [{ record: a, claim: 'Claim logout again', content: 'The cited passage sits here.' }]);
      await jumpTo(page, 1);
      await expect(rendererNode(page)).toBeVisible({ timeout: 60_000 });
      await log.settled();
      const [call] = log.forRecord(a.id);
      expect(call.status).toBe(200);
      expect(call.ifNoneMatch, 'a new session should not send the old validator').toBeNull();
    } finally {
      await context.close();
    }
  });

  test('a second user in the same browser gets no cached content', async ({ browser, apiContext }) => {
    test.setTimeout(420_000);
    expect(await ensureSmtpConfigured(apiContext), 'SMTP must be configured to invite the second user').toBe(true);
    const secret = await upload(kbId, `private-${stamp}.txt`, MIME.txt, Buffer.from(`PRIVATE-${stamp} ${'confidential '.repeat(50)}`));
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;
    expect(email && password, 'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set').toBeTruthy();
    const orgName = await orgNameOf(apiContext);
    const memberEmail = uniqueMemberEmail('viewer-cache');
    await inviteMember(apiContext, memberEmail);

    try {
      const member = await acceptInvite(browser, memberEmail);
      const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
      try {
        const page = await context.newPage();
        await loginViaUI(page, email!, password!);
        await askCitedQuestion(page, [{ record: secret, claim: 'Claim private', content: `PRIVATE-${stamp}` }]);
        await jumpTo(page, 1);
        await expect(page.getByText(`PRIVATE-${stamp}`).first()).toBeVisible({ timeout: 60_000 });
        await logoutViaUi(page, orgName);

        // The second user signs in in the same browser context and opens the record by its citation.
        await page.goto('/login/');
        await loginViaUI(page, member.email, member.password);
        const log = recordStreamLog(page);
        await askCitedQuestion(page, [{ record: secret, claim: 'Claim private again', content: `PRIVATE-${stamp}` }]);
        await jumpTo(page, 1);
        // The details call refuses first, so no stream request is expected; give any stray one time to show.
        await page.waitForTimeout(5_000);
        await log.settled();
        expect(log.bodies(secret.id), 'the second user must receive no bytes').toHaveLength(0);
        await expect(page.getByText(`PRIVATE-${stamp}`, { exact: false }), 'no content of the first user in the page').toHaveCount(0);
      } finally {
        await context.close();
      }
    } finally {
      await deleteUserByEmail(apiContext, memberEmail);
    }
  });

  // ── A connector record ─────────────────────────────────────────────────

  test('a connector record is reused inside the window', async ({ page, apiContext }) => {
    test.setTimeout(420_000);
    // The stack needs a Confluence connector pointed at the connectors simulator, synced once.
    const sim = process.env.E2E_CONFLUENCE_SIM_URL ?? 'http://localhost:8101';
    const ids = (process.env.E2E_CONNECTOR_RECORD_IDS ?? '').split(',').filter(Boolean);
    const connectorId = process.env.E2E_CONNECTOR_ID ?? '';
    const connectorName = process.env.E2E_CONNECTOR_NAME ?? 'CONFLUENCE';
    const pageId = process.env.E2E_CONNECTOR_PAGE_ID ?? '';

    const probe = await request.newContext();
    const health = await probe.get(`${sim}/_sim/health`).catch((e: Error) => e);
    expect(health instanceof Error ? health.message : health.status(), `the Confluence simulator should answer at ${sim}`).toBe(200);
    expect(ids.length, 'E2E_CONNECTOR_RECORD_IDS must list two synced connector record ids').toBeGreaterThanOrEqual(2);
    expect(connectorId && pageId, 'E2E_CONNECTOR_ID and E2E_CONNECTOR_PAGE_ID must name the connector and the page to edit').toBeTruthy();

    const records: CitedRecord[] = [];
    for (const id of ids.slice(0, 2)) {
      const r = await apiContext.get(`/api/v1/knowledgeBase/record/${id}`);
      expect(r.ok(), `record ${id} should exist`).toBe(true);
      const rec = ((await r.json()) as { record: { recordName: string; mimeType?: string; origin?: string } }).record;
      expect(rec.origin, 'a connector record').toBe('CONNECTOR');
      records.push({ id, name: rec.recordName, mimeType: rec.mimeType ?? 'text/html', extension: 'html' });
    }
    const [a, b] = records;

    const log = recordStreamLog(page);
    const cites: Cite[] = [
      { record: a, claim: 'Connector claim one', content: 'first' },
      { record: a, claim: 'Connector claim two', content: 'second' },
      { record: b, claim: 'Other record claim', content: 'other' },
    ];
    await askCitedQuestion(page, cites);
    for (const n of [1, 2, 1, 2, 1]) await jumpTo(page, n);
    await expect(rendererNode(page)).toBeVisible({ timeout: 60_000 });
    await jumpTo(page, 3);
    await expect(page.getByText(b.name).first()).toBeVisible({ timeout: 60_000 });
    await jumpTo(page, 1);
    await expect(rendererNode(page)).toBeVisible({ timeout: 15_000 });
    await log.settled();
    expect(log.bodies(a.id), 'ten jumps and an A, B, A re-open inside the window should stream A once').toHaveLength(1);

    // The page changes at the source and the connector syncs: the label moves, so a re-open is a full answer.
    const revisionBefore = await currentRevision(apiContext, a.id);
    const edit = await probe.put(`${sim}/wiki/rest/api/content/${pageId}`, { data: { version: { number: 2 }, title: a.name, type: 'page', body: { storage: { value: `<p>edited ${stamp}</p>`, representation: 'storage' } } } });
    expect(edit.ok(), `editing the simulator page answered ${edit.status()}`).toBe(true);
    const resync = await apiContext.post(`/api/v1/connectors/${connectorId}/resync`, { data: { connectorName, fullSync: true } });
    expect(resync.ok(), `resync answered ${resync.status()}`).toBe(true);
    await expect.poll(() => currentRevision(apiContext, a.id), { timeout: 180_000, intervals: [5_000] }).not.toBe(revisionBefore);

    await jumpTo(page, 3);
    await expect(page.getByText(b.name).first()).toBeVisible({ timeout: 60_000 });
    log.reset();
    await jumpTo(page, 1);
    await expect.poll(async () => { await log.settled(); return log.bodies(a.id).length; }, { timeout: 60_000 }).toBe(1);
  });

  // ── Memory ─────────────────────────────────────────────────────────────

  // ── A large PDF is read by ranges ──────────────────────────────────────

  test('a large PDF loads by range', async ({ page }) => {
    test.setTimeout(600_000);
    // The platform's upload limit bounds this file; raise it (and E2E_LARGE_PDF_MIB) to run the 200 MB case.
    const mib = Number(process.env.E2E_LARGE_PDF_MIB ?? 40);
    expect(mib, 'a range-mode PDF is larger than 32 MiB').toBeGreaterThan(32);
    const pages = 400;
    const pdf = await upload(kbId, `atlas-${stamp}.pdf`, MIME.pdf, makePdf(pages, mib * MIB));
    const log = recordStreamLog(page);
    await page.goto(`/record/${pdf.id}`);
    await expect.poll(async () => visiblePdfPage(page), { timeout: 120_000 }).toBe(1);
    await log.settled();

    // Page 1 costs the first range and what pdf.js asked for next, nothing like the whole file.
    const opening = log.forRecord(pdf.id);
    expect(opening.length, 'the viewer asked for the record').toBeGreaterThan(0);
    expect(opening.every((c) => c.status === 206 && c.range !== null), 'every request is a partial one').toBe(true);
    expect(opening[0].range).toBe(`bytes=0-${MIB - 1}`);
    const bytesForPageOne = opening.reduce((n, c) => n + c.bytes, 0);
    expect(bytesForPageOne, 'page 1 arrives after at most 4 MiB').toBeLessThanOrEqual(4 * MIB);

    // No background fetch: nothing more is asked for while the viewer sits still.
    const asked = log.forRecord(pdf.id).length;
    await page.waitForTimeout(10_000);
    await log.settled();
    expect(log.forRecord(pdf.id).length, 'no request beyond the viewed pages').toBe(asked);

    // A far page costs only its own ranges; pages 1 to 5 again cost none.
    await scrollViewer(page, 'bottom');
    await expect.poll(async () => visiblePdfPage(page), { timeout: 120_000 }).toBeGreaterThan(pages - 20);
    await log.settled();
    const total = log.forRecord(pdf.id).reduce((n, c) => n + c.bytes, 0);
    expect(total, 'the far page did not load the file').toBeLessThan(mib * MIB * 0.5);
    const afterFar = log.forRecord(pdf.id).length;
    await scrollViewer(page, 'top');
    await expect.poll(async () => visiblePdfPage(page), { timeout: 60_000 }).toBeLessThanOrEqual(5);
    await log.settled();
    expect(log.forRecord(pdf.id).length, 'revisits of pages 1 to 5 make no new range requests').toBe(afterFar);
  });

  test('the cache stays within its budget', async ({ page }) => {
    test.setTimeout(600_000);
    const records: CitedRecord[] = [];
    for (let i = 0; i < 30; i++) records.push(await upload(kbId, `budget-${i}-${stamp}.pdf`, MIME.pdf, makePdf(1 + i, 10 * MIB)));
    const log = recordStreamLog(page);
    await askCitedQuestion(page, records.map((r, i) => ({ record: r, claim: `Budget claim ${i + 1}`, content: 'PAGE-1', pageNum: [1] })));

    let peakHeap = 0;
    for (let i = 0; i < records.length; i++) {
      await jumpTo(page, i + 1);
      await expect.poll(async () => { await log.settled(); return log.bodies(records[i].id).length; }, { timeout: 60_000 }).toBe(1);
      peakHeap = Math.max(peakHeap, await usedHeap(page));
    }
    log.reset();
    // 30 x 10 MB is far over the budget: the first record was evicted and comes back in full.
    await jumpTo(page, 1);
    await expect.poll(async () => { await log.settled(); return log.bodies(records[0].id).length; }, { timeout: 60_000 }).toBe(1);
    const [again] = log.forRecord(records[0].id);
    expect(again.ifNoneMatch, 'an evicted record has no validator to send').toBeNull();
    test.info().annotations.push({ type: 'peak-js-heap-bytes', description: String(peakHeap) });
    expect(peakHeap, 'the tab should not hold anything near all 300 MB of content in the JS heap').toBeLessThan(1024 * MIB);
  });
});

// ── helpers local to this spec ───────────────────────────────────────────

async function orgNameOf(apiContext: APIRequestContext): Promise<string> {
  const r = await apiContext.get('/api/v1/org');
  expect(r.ok(), `reading the organisation answered ${r.status()}`).toBe(true);
  const org = (await r.json()) as { shortName?: string; registeredName?: string };
  const name = org.shortName || org.registeredName;
  expect(name, 'the organisation should have a name to find the workspace menu by').toBeTruthy();
  return name!;
}

async function inviteMember(apiContext: APIRequestContext, email: string): Promise<void> {
  const r = await apiContext.post('/api/v1/users/bulk/invite', { data: { emails: [email] } });
  expect(r.ok(), `inviting ${email} answered ${r.status()}`).toBe(true);
}

async function usedHeap(page: import('@playwright/test').Page): Promise<number> {
  return page.evaluate(() => (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? 0);
}

async function currentRevision(apiContext: APIRequestContext, id: string): Promise<string> {
  const r = await apiContext.get(`/api/v1/knowledgeBase/record/${id}`);
  return String(((await r.json()) as { record: { externalRevisionId?: string } }).record.externalRevisionId ?? null);
}

