/** QA W3: `/download` of the whole workspace names the zip from the chat title as one clean, short line. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TFunction } from 'i18next';

const saved = vi.hoisted(() => ({ downloads: [] as string[] }));
vi.mock('@/app/components/file-preview/utils', () => ({
  downloadPreviewFile: (input: { name: string }) => saved.downloads.push(input.name),
}));
vi.mock('../api', () => ({
  ChatApi: { downloadWorkspaceArchive: vi.fn(async () => ({ blob: new Blob(['z']), revision: 1 })) },
  statusOf: vi.fn(),
}));
vi.mock('../streaming', () => ({ followDurableRunInSlot: vi.fn() }));

import { download } from '../sandbox-commands';

const t = ((key: string) => key) as unknown as TFunction;
const session = { sessionId: 's-1' } as never;
const row = { rowKey: 'r', afterPairs: 0, typed: '/download' };

beforeEach(() => {
  saved.downloads.length = 0;
});

describe('/download zip name', () => {
  it('a two-line first message becomes one short line, not an underscore', async () => {
    await download(session, '', { folder: false, title: 'qa78-sb admin sandbox files\nsay Ready for files.' }, row, t);
    expect(saved.downloads).toEqual(['qa78-sb admin sandbox files say Ready-v1.zip']);
  });

  it('a title with nothing usable is named workspace', async () => {
    await download(session, '', { folder: false, title: ' \n/// ' }, row, t);
    expect(saved.downloads).toEqual(['workspace-v1.zip']);
  });
});
