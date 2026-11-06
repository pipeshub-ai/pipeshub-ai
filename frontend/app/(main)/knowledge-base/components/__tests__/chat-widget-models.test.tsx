import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/knowledge-base',
}));

const composer = vi.hoisted(() => ({ rendered: false }));
vi.mock('@/chat/components/chat-input', () => ({
  ChatInput: () => {
    composer.rendered = true;
    return null;
  },
}));

const models = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({ fetchModelsForContext: models.fetch }));

import { ChatWidgetWrapper } from '../chat-widget-wrapper';
import { ASSISTANT_CTX } from '@/chat/store';

beforeEach(() => {
  composer.rendered = false;
  models.fetch.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("a collection's chat widget", () => {
  it('loads the configured models as the chat page does, so its model chip names the model before the panel opens', () => {
    render(<ChatWidgetWrapper currentTitle="Plans" selectedKbId="kb-1" isAllRecordsMode={false} />);

    expect(models.fetch).toHaveBeenCalledWith(ASSISTANT_CTX);
  });

  it('still renders when the models cannot be loaded; the model panel shows that error itself', async () => {
    models.fetch.mockRejectedValue(new Error('offline'));
    render(<ChatWidgetWrapper currentTitle="Plans" selectedKbId="kb-1" isAllRecordsMode={false} />);
    await Promise.resolve();

    expect(composer.rendered).toBe(true);
  });
});
