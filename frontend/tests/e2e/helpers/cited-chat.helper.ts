import { expect, type FrameLocator, type Locator, type Page } from '@playwright/test';
import { buildAguiConversation } from '../chat/agui-sse-builder';

/**
 * A chat whose answer cites real records. Only the model's answer is faked (the stream route); the
 * records, the details call and the record stream are the real server's, so the viewer's traffic is real.
 */

export interface CitedRecord {
  id: string;
  name: string;
  mimeType: string;
  extension: string;
}

export interface Cite {
  record: CitedRecord;
  /** Text shown in the answer next to the chip. */
  claim: string;
  /** Snippet the citation carries (and the viewer highlights). */
  content: string;
  pageNum?: number[];
  blockNum?: number[];
}

const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'internal_search', modelFriendlyName: 'GPT-4o mini' };

function frame(type: string, fields: Record<string, unknown> = {}): string {
  return `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;
}

export function buildCitedAnswer(cites: Cite[]): { answer: string; citations: unknown[] } {
  const answer = cites.map((c, i) => `${c.claim} [${i + 1}]`).join('\n\n');
  const citations = cites.map((c, i) => ({
    citationId: `cit-${i + 1}`,
    citationData: {
      content: c.content,
      chunkIndex: i + 1,
      citationType: 'vectordb|document',
      metadata: {
        recordId: c.record.id,
        recordName: c.record.name,
        mimeType: c.record.mimeType,
        extension: c.record.extension,
        connector: 'KB',
        recordType: 'FILE',
        origin: 'UPLOAD',
        previewRenderable: true,
        pageNum: c.pageNum,
        blockNum: c.blockNum,
      },
    },
  }));
  return { answer, citations };
}

let seq = 0;

/** Sends one question in a new chat and waits for the cited answer to be on screen. */
export async function askCitedQuestion(page: Page, cites: Cite[]): Promise<Locator> {
  const { answer, citations } = buildCitedAnswer(cites);
  const id = `${Date.now().toString(36)}-${++seq}`;
  const conversation = buildAguiConversation({
    conversationId: `conv-cv-${id}`,
    userMessageId: `msg-user-${id}`,
    botMessageId: `msg-bot-${id}`,
    question: 'Where is this written?',
    answer,
    modelInfo: MODEL_INFO,
  });
  const bot = conversation.messages.find((m) => m.messageType === 'bot_response');
  if (!bot) throw new Error('no bot message in the conversation');
  bot.citations = citations;
  const body = [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId: conversation._id, title: 'Where is this written?' } }),
    frame('TEXT_MESSAGE_START'),
    frame('TEXT_MESSAGE_CONTENT', { delta: answer }),
    frame('TEXT_MESSAGE_END'),
    frame('RUN_FINISHED', { result: { conversation, meta: { requestId: `req-${id}`, timestamp: new Date().toISOString(), duration: 10 } } }),
  ].join('');

  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', message: 'Success', models: [{ modelType: 'chat', provider: 'openAI', modelName: 'GPT-4o mini', modelKey: 'gpt-4o-mini', isMultimodal: false, isReasoning: false, isDefault: true, modelFriendlyName: 'GPT-4o mini' }] }) }),
  );
  await page.route('**/api/v1/conversations/stream', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' }, body })
      : route.continue(),
  );

  await page.goto('/chat/');
  const input = page.locator('textarea').last();
  await input.fill('Where is this written?');
  await input.press('Enter');
  const first = cites[0].claim;
  await expect(page.getByText(first, { exact: false }).first()).toBeVisible({ timeout: 30_000 });
  return page.getByText(first, { exact: false }).first();
}

/** The numbered chip of citation `n` (1-based) in the answer. */
export function chip(page: Page, n: number): Locator {
  return page.getByRole('button', { name: String(n), exact: true }).first();
}

/**
 * Click citation chip `n`. The first click opens a card whose Preview button opens the viewer; when the card
 * appears its button is pressed, and when the viewer is already open the click just jumps.
 */
export async function jumpTo(page: Page, n: number): Promise<void> {
  await chip(page, n).click();
  const preview = page.getByRole('button', { name: 'Preview', exact: true }).first();
  try {
    await preview.waitFor({ state: 'visible', timeout: 1_500 });
  } catch {
    return;
  }
  await preview.click({ timeout: 5_000 }).catch(() => undefined);
}

/** The viewer's renderer node. The test fails, not skips, when the preview has not mounted one. */
export function rendererNode(page: Page): Locator {
  return page.locator('[data-preview-renderer]').first();
}

/**
 * The document a docx or HTML preview draws inside its sandboxed frame. The frame's origin is opaque, so
 * the page's own scripts cannot reach it; its text is found and scrolled through the frame instead.
 */
export function previewFrame(page: Page): FrameLocator {
  return rendererNode(page).locator('iframe').first().contentFrame();
}

/** Scrolls every scrollable part of the document `locator` lives in to its top or bottom. */
export async function scrollDocumentOf(locator: Locator, to: 'top' | 'bottom'): Promise<void> {
  await locator.evaluate((el, where) => {
    const doc = el.ownerDocument;
    const nodes = [doc.scrollingElement, ...Array.from(doc.querySelectorAll('*'))].filter(Boolean) as HTMLElement[];
    for (const node of nodes) {
      if (node.scrollHeight > node.clientHeight + 5) node.scrollTop = where === 'top' ? 0 : node.scrollHeight;
    }
  }, to);
}

/**
 * Waits for the preview's frame to finish loading. The viewer sends the active citation to the frame again
 * on `load`, and the frame scrolls to it, so a scroll started before then can be followed by another.
 */
export async function waitForPreviewFrameLoad(page: Page): Promise<void> {
  const handle = await rendererNode(page).locator('iframe').first().elementHandle();
  const frame = await handle?.contentFrame();
  if (!frame) throw new Error('the preview shows no frame');
  await frame.waitForLoadState('load');
}

/**
 * Asserts the element stays out of view for `ms`. A scroll that brings it back on its own before the next click
 * fails here, so that click is the only thing a later "back in view" check can credit.
 */
export async function expectStaysOutOfView(locator: Locator, ms = 1_000): Promise<void> {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    expect(await isInView(locator), 'the element came back into view on its own').toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

/** Waits until the document `locator` lives in stops scrolling: a smooth scroll still running would move it later. */
export async function waitForScrollToSettle(locator: Locator): Promise<void> {
  let last: number | null = null;
  await expect
    .poll(
      async () => {
        const top = await locator.evaluate((el) => el.ownerDocument.scrollingElement?.scrollTop ?? 0);
        const settled = top === last;
        last = top;
        return settled;
      },
      { message: 'the document should stop scrolling', timeout: 15_000, intervals: [300] },
    )
    .toBe(true);
}

/**
 * A handle to the renderer's document node, to prove later that it was never replaced.
 * The first citation click opens a card whose Preview button opens the viewer; once the viewer is open, later
 * clicks jump inside it. So the card's button is pressed when it is showing and the renderer is not.
 */
export async function holdRenderer(page: Page) {
  const node = rendererNode(page);
  const preview = page.getByRole('button', { name: 'Preview', exact: true }).first();
  await expect
    .poll(
      async () => {
        if (await node.isVisible()) return true;
        if (await preview.isVisible()) await preview.click({ timeout: 5_000 }).catch(() => undefined);
        return node.isVisible();
      },
      { message: 'the preview should show a renderer', timeout: 60_000, intervals: [250, 500, 1000] },
    )
    .toBe(true);
  return node.elementHandle();
}

export async function isAttached(handle: Awaited<ReturnType<typeof holdRenderer>>): Promise<boolean> {
  return handle ? handle.evaluate((el) => el.isConnected) : false;
}

/** True when the element is inside the visible part of its nearest scrolling ancestor and the window. */
export async function isInView(locator: Locator): Promise<boolean> {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect();
    let parent: HTMLElement | null = el.parentElement;
    let box = { top: 0, bottom: window.innerHeight };
    while (parent) {
      const s = getComputedStyle(parent);
      if (/(auto|scroll)/.test(s.overflowY) && parent.scrollHeight > parent.clientHeight) {
        const pr = parent.getBoundingClientRect();
        box = { top: Math.max(box.top, pr.top), bottom: Math.min(box.bottom, pr.bottom) };
        break;
      }
      parent = parent.parentElement;
    }
    return r.bottom > box.top && r.top < box.bottom;
  });
}

/** The page the pdf.js viewer shows most of. */
export async function visiblePdfPage(page: Page): Promise<number> {
  return page.evaluate(() => {
    const pages = Array.from(document.querySelectorAll<HTMLElement>('[data-preview-renderer] .page[data-page-number]'));
    let best = 0;
    let bestVisible = 0;
    for (const p of pages) {
      const r = p.getBoundingClientRect();
      const visible = Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
      if (visible > bestVisible) {
        bestVisible = visible;
        best = Number(p.dataset.pageNumber);
      }
    }
    return best;
  });
}

/** Scrolls every scrollable part of the viewer to its top or bottom. */
export async function scrollViewer(page: Page, to: 'top' | 'bottom'): Promise<void> {
  await page.evaluate((where) => {
    const root = document.querySelector('[data-preview-renderer]');
    if (!root) throw new Error('no renderer to scroll');
    const nodes = [root, ...Array.from(root.querySelectorAll('*'))] as HTMLElement[];
    for (const el of nodes) {
      if (el.scrollHeight > el.clientHeight + 5 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) {
        el.scrollTop = where === 'top' ? 0 : el.scrollHeight;
      }
    }
  }, to);
}

/** Signs out through the workspace menu: the footer's organisation button, then "Log out". */
export async function logoutViaUi(page: Page, orgName: string): Promise<void> {
  await page.getByText(orgName, { exact: true }).last().click();
  await page.getByText('Log out', { exact: true }).click();
  await page.waitForURL('**/login/**', { timeout: 20_000 });
}
