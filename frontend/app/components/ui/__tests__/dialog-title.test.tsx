/**
 * R31AXEU-FU-1: a dialog named by a hidden English title reads "Complete Your Profile" to a screen reader in every
 * language while the screen says something else. DLGTITLE-1/2 read every dialog in the classic UI: each is named (a
 * Dialog.Title, the shared DialogHeading, or aria-labelledby/aria-label on its content), and no visually hidden title
 * holds words that do not go through `t()`. DLGTITLE-4..6 render the three that had one: each is named by its
 * visible, translated title.
 */
import React from 'react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/app/(main)/workspace/profile/api', () => ({
  ProfileApi: { updateUser: vi.fn() },
  getUserIdFromToken: () => 'u1',
  getUserEmailFromToken: () => 'a@b.c',
}));
vi.mock('@/lib/hooks/use-upload-limits', () => ({ useUploadLimits: () => ({ maxFileSizeBytes: 1 << 20, maxFileSizeMB: 1 }) }));

import { FullNameDialog } from '@/app/(main)/components/full-name-dialog';
import { ArchiveChatDialog } from '@/app/(main)/chat/sidebar/dialogs/archive-chat-dialog';
import { ReplaceFileDialog } from '@/app/(main)/knowledge-base/components/dialogs/replace-file-dialog';
import type { KnowledgeHubNode } from '@/app/(main)/knowledge-base/types';

const FRONTEND = path.resolve(__dirname, '../../../..');
const ROOTS = ['app', 'lib', 'components'];

function sources(rel: string): string[] {
  const at = path.join(FRONTEND, rel);
  if (statSync(at).isFile()) return /\.tsx$/.test(rel) && !/\.(test|spec)\./.test(rel) ? [rel] : [];
  return readdirSync(at).flatMap((name) => (name === '__tests__' || name === 'node_modules' ? [] : sources(path.join(rel, name))));
}

const NAMED = /(?:Dialog|AlertDialog)\.Title\b|<DialogHeading\b|<TypedDeleteDialog\b/;

/** Whether a file that renders a dialog's content names it. */
function namesItsDialog(source: string): boolean {
  if (NAMED.test(source)) return true;
  const contents = [...source.matchAll(/<(?:Dialog|AlertDialog)\.Content\b([^>]*)>/g)].map((m) => m[1]);
  return contents.length > 0 && contents.every((attrs) => /aria-(?:labelledby|label)=/.test(attrs));
}

/** The words of each visually hidden title that are not a `t()` call: literal text or a quoted fallback. */
function hiddenLiteralTitles(source: string): string[] {
  const hidden = [...source.matchAll(/<VisuallyHidden\b[^>]*>([\s\S]*?)<\/VisuallyHidden>/g)].map((m) => m[1]);
  return hidden.flatMap((block) =>
    [...block.matchAll(/<((?:Dialog|AlertDialog)\.Title)\b[^>]*>([\s\S]*?)<\/\1>/g)].flatMap((m) => {
      const words = m[2].replace(/\bt\((?:[^()]|\([^()]*\))*\)/g, '');
      const literal = words.replace(/\{[\s\S]*?\}/g, '').trim();
      const quoted = [...words.matchAll(/\{[\s\S]*?\}/g)].flatMap((e) => [...e[0].matchAll(/(['"`])([^'"`]*[A-Za-z][^'"`]*)\1/g)].map((q) => q[2]));
      return [...(/[A-Za-z]/.test(literal) ? [literal] : []), ...quoted];
    }),
  );
}

const DIALOG_FILES = ROOTS.flatMap(sources)
  .map((file) => [file, readFileSync(path.join(FRONTEND, file), 'utf8')] as const)
  .filter(([, source]) => /<(?:Dialog|AlertDialog)\.Content\b/.test(source));

afterEach(() => cleanup());

describe('dialog names across the classic UI', () => {
  it('DLGTITLE-1: every dialog is named', () => {
    expect(DIALOG_FILES.length).toBeGreaterThan(50);
    expect(DIALOG_FILES.filter(([, source]) => !namesItsDialog(source)).map(([file]) => file)).toEqual([]);
  });

  it('DLGTITLE-2: no visually hidden title holds untranslated words', () => {
    const found = DIALOG_FILES.flatMap(([file, source]) => hiddenLiteralTitles(source).map((words) => `${file}: ${words}`));
    expect(found).toEqual([]);
  });

  it('DLGTITLE-3: the check finds a hidden English title, an English fallback and a dialog with no name', () => {
    const hiddenEnglish = `<VisuallyHidden>\n  <Dialog.Title>Archive Confirmation</Dialog.Title>\n</VisuallyHidden>`;
    const fallback = `<VisuallyHidden><Dialog.Title>{node?.name || 'Folder Details'}</Dialog.Title></VisuallyHidden>`;
    const translated = `<VisuallyHidden><Dialog.Title>{t('dialog.moveFolderLocation', { defaultValue: 'Move' })}</Dialog.Title></VisuallyHidden>`;
    expect(hiddenLiteralTitles(hiddenEnglish)).toEqual(['Archive Confirmation']);
    expect(hiddenLiteralTitles(fallback)).toEqual(['Folder Details']);
    expect(hiddenLiteralTitles(translated)).toEqual([]);
    expect(namesItsDialog('<Dialog.Root><Dialog.Content>x</Dialog.Content></Dialog.Root>')).toBe(false);
    expect(namesItsDialog('<Dialog.Content aria-labelledby={id}>x</Dialog.Content>')).toBe(true);
  });
});

const within = (ui: React.ReactElement) => render(<Theme>{ui}</Theme>);

/** The dialog's name is text a sighted person sees: not inside a visually hidden wrapper. */
function visibleName(dialog: HTMLElement): string {
  const title = document.getElementById(dialog.getAttribute('aria-labelledby') ?? '');
  expect(title, 'the dialog is labelled by an element').toBeTruthy();
  expect(title!.closest('.rt-VisuallyHidden, [data-radix-visually-hidden]')).toBeNull();
  return title!.textContent ?? '';
}

describe('the dialogs that had a hidden English title', () => {
  it('DLGTITLE-4: the profile dialog is named by its visible title, and its field by its label', () => {
    within(<FullNameDialog open onSuccess={() => {}} />);
    expect(visibleName(screen.getByRole('dialog'))).toBe(en.fullNameDialog.title);
    expect(screen.getByRole('textbox', { name: en.fullNameDialog.label })).toBeTruthy();
    expect(screen.getByRole('button', { name: en.fullNameDialog.submit })).toBeTruthy();
  });

  it('DLGTITLE-5: the archive dialog is named by its question and described by what it archives', () => {
    within(<ArchiveChatDialog open onOpenChange={() => {}} onConfirm={async () => {}} chatTitle="Q3 plans" />);
    const dialog = screen.getByRole('dialog');
    expect(visibleName(dialog)).toBe(en.chat.archiveChatTitle);
    const description = document.getElementById(dialog.getAttribute('aria-describedby') ?? '');
    expect(description?.textContent).toContain('Q3 plans');
  });

  it('DLGTITLE-6: the replace-file panel is named by its visible header', () => {
    const item = { id: 'r1', name: 'plan.pdf', nodeType: 'record', extension: 'pdf' } as unknown as KnowledgeHubNode;
    within(<ReplaceFileDialog open onOpenChange={() => {}} item={item} onReplace={() => {}} />);
    expect(visibleName(screen.getByRole('dialog'))).toBe(en.dialog.replaceFile);
  });
});
