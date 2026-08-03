import { chromium } from '@playwright/test';

const BASE_URL = 'http://localhost:3000';

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj)).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

const fakeAccessToken = `${b64url({ alg: 'none' })}.${b64url({
  userId: 'test-user-1',
  email: 'test@example.com',
  accountType: 'individual',
  exp: Math.floor(Date.now() / 1000) + 3600,
})}.fakesig`;

const MOCK_LLMS_RESPONSE = {
  status: 'success',
  models: [
    {
      modelType: 'chat',
      provider: 'azureOpenAI',
      modelName: 'gpt-5.4-mini',
      modelKey: 'gpt-5.4-mini',
      isMultimodal: true,
      isReasoning: true,
      isDefault: true,
      modelFriendlyName: 'gpt-5.4-mini',
    },
  ],
  message: 'Success',
};

const MOCK_CONVERSATIONS_RESPONSE = {
  conversations: [],
  source: 'owned',
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

async function mockBaselineApis(page) {
  // Catch-all safety net first (lower priority — later registrations win on overlap).
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success' }) }),
  );

  await page.route('**/api/v1/health', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy', services: {} }) }),
  );
  await page.route('**/api/v1/health/services', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy', services: {} }) }),
  );
  await page.route('**/api/v1/org/onboarding-status', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'configured' }) }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLMS_RESPONSE) }),
  );
  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_CONVERSATIONS_RESPONSE) });
    }
    return route.continue();
  });
  await page.route('**/api/v1/users/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ _id: 'test-user-1', fullName: 'Test User', email: 'test@example.com', hasLoggedIn: true }) }),
  );
  await page.route('**/api/v1/userGroups/users/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
  );
}

async function run() {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  await context.addInitScript((token) => {
    window.localStorage.setItem('jwt_access_token', token);
    window.localStorage.setItem('jwt_refresh_token', token);
  }, fakeAccessToken);

  const page = await context.newPage();
  await mockBaselineApis(page);

  page.on('pageerror', (err) => console.log('PAGE ERROR:', err.message));

  const viewports = {
    mobile: { width: 390, height: 844 },
    tablet: { width: 820, height: 1180 },
    desktop: { width: 1440, height: 900 },
  };

  for (const [name, size] of Object.entries(viewports)) {
    await page.setViewportSize(size);
    await page.goto(`${BASE_URL}/chat/`, { waitUntil: 'domcontentloaded' });
    try {
      await page.waitForSelector('textarea', { timeout: 20000 });
    } catch {
      console.log(`[${name}] textarea not found within timeout`);
    }
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `/tmp/chat-${name}.png` });
    console.log(`[${name}] screenshot saved: /tmp/chat-${name}.png`);

    // Open the "+" menu to verify it too.
    const plusBtn = page.locator('button[aria-label*="Attach files"]').first();
    if (await plusBtn.count() > 0) {
      await plusBtn.click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `/tmp/chat-${name}-plus-open.png` });
      console.log(`[${name}] plus-menu screenshot saved: /tmp/chat-${name}-plus-open.png`);
    } else {
      console.log(`[${name}] plus button NOT FOUND`);
    }
  }

  await browser.close();
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
