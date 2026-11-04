/**
 * The project Files card reloads its list after an upload. When a sign-out
 * cancelled the upload it must not: the reload (and the request creating the
 * project's storage) would run in the next person's session.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const upload = vi.hoisted(() => ({ uploadFiles: vi.fn() }));
const projectApi = vi.hoisted(() => ({ ensureKnowledgeBase: vi.fn() }));
const hubApi = vi.hoisted(() => ({ getNodeChildren: vi.fn() }));
vi.mock('../../hooks/use-kb-stream-upload', () => ({ useKbStreamUpload: () => upload }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: projectApi }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeHubApi: hubApi, KnowledgeBaseApi: {} }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { FilesCard } from '../files-card';

function chooseFile(container: HTMLElement) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(['x'], 'a.txt')] } });
}

async function uploadAndSettle(finished: boolean) {
  upload.uploadFiles.mockResolvedValue(finished);
  projectApi.ensureKnowledgeBase.mockResolvedValue('kb-1');
  hubApi.getNodeChildren.mockResolvedValue({ items: [] });
  const { container } = render(
    <Theme>
      <FilesCard projectId="p1" linkedKnowledgeBaseId={null} canEdit onKbCreated={vi.fn()} />
    </Theme>,
  );
  chooseFile(container);
  await act(async () => {});
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the project Files card after an upload', () => {
  it('reloads its list when the upload finished', async () => {
    await uploadAndSettle(true);

    expect(projectApi.ensureKnowledgeBase).toHaveBeenCalledWith('p1');
    expect(hubApi.getNodeChildren).toHaveBeenCalledTimes(1);
  });

  it('sends nothing more when a sign-out cancelled the upload', async () => {
    await uploadAndSettle(false);

    expect(projectApi.ensureKnowledgeBase).not.toHaveBeenCalled();
    expect(hubApi.getNodeChildren).not.toHaveBeenCalled();
  });
});
