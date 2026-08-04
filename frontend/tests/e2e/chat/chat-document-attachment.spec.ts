import { test, expect } from '../fixtures/base.fixture';

const MOCK_LLMS_RESPONSE = {
  status: 'success',
  models: [
    {
      modelType: 'chat',
      provider: 'openAI',
      modelName: 'GPT-4o mini',
      modelKey: 'gpt-4o-mini',
      isMultimodal: false,
      isReasoning: false,
      isDefault: true,
      modelFriendlyName: 'GPT-4o mini',
    },
  ],
  message: 'Success',
};

const MOCK_CONVERSATIONS_RESPONSE = {
  conversations: [],
  source: 'owned',
  pagination: {
    page: 1,
    limit: 20,
    totalCount: 0,
    totalPages: 0,
    hasNextPage: false,
    hasPrevPage: false,
  },
};

async function mockBaselineApis(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_LLMS_RESPONSE),
    }),
  );

  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_CONVERSATIONS_RESPONSE),
      });
    }
    return route.continue();
  });
}

function attachmentRef(overrides: {
  recordId: string;
  recordName: string;
  mimeType: string;
  extension: string;
}) {
  return {
    recordId: overrides.recordId,
    recordName: overrides.recordName,
    mimeType: overrides.mimeType,
    extension: overrides.extension,
    virtualRecordId: `vrid-${overrides.recordId}`,
  };
}

async function openAttachFiles(page: import('@playwright/test').Page) {
  const plusButton = page.getByRole('button', { name: 'Attach files and capabilities' });
  await expect(plusButton).toBeVisible({ timeout: 5_000 });
  await plusButton.click();
  const attachItem = page.getByText('Attach files', { exact: true });
  await expect(attachItem).toBeVisible({ timeout: 5_000 });
  await attachItem.click();
}

test.describe('Chat — document attachments (DOCX / XLSX / CSV)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('DOCX attachment is accepted by the file picker and shows a chip after upload', async ({ page }) => {
    await page.route('**/api/v1/conversations/attachments/upload', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          attachments: [
            attachmentRef({
              recordId: 'rec-docx-1',
              recordName: 'brief.docx',
              mimeType:
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              extension: 'docx',
            }),
          ],
        }),
      });
    });

    await openAttachFiles(page);
    const fileInput = page.locator('input[type="file"]').first();
    await expect(fileInput).toBeAttached({ timeout: 5_000 });
    const accept = (await fileInput.getAttribute('accept')) || '';
    expect(accept).toContain('.docx');
    expect(accept).toContain(
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    );

    // Minimal ZIP/OOXML magic bytes so the File object is non-empty.
    await fileInput.setInputFiles({
      name: 'brief.docx',
      mimeType:
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: Buffer.from('PK\x03\x04fake-docx-content'),
    });

    await expect(page.getByText('brief.docx').first()).toBeVisible({ timeout: 10_000 });
  });

  test('upload 415 keeps the composer usable without crashing', async ({ page }) => {
    await page.route('**/api/v1/conversations/attachments/upload', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 415,
        contentType: 'application/json',
        body: JSON.stringify({
          message: 'Unsupported attachment type',
        }),
      });
    });

    await openAttachFiles(page);
    const fileInput = page.locator('input[type="file"]').first();
    await fileInput.setInputFiles({
      name: 'sales.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('a,b\n1,2\n'),
    });

    await expect(page.locator('textarea').last()).toBeVisible({ timeout: 10_000 });
  });
});
