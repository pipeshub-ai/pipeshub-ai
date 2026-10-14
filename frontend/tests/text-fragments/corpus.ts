import fs from 'node:fs';
import path from 'node:path';

export const CORPUS_DIR = path.resolve(
  __dirname,
  '../../../backend/python/tests/fixtures/text_fragments',
);

export const FAKE_ORIGIN = 'https://pages.example.test';

export interface GoldenCase {
  id: string;
  fixture: string;
  format: 'markdown' | 'html' | 'plain';
  snippet: string;
  base_url?: string;
  expected_url: string;
  expected_highlight: string | null;
}

export function loadCases(): GoldenCase[] {
  return JSON.parse(fs.readFileSync(path.join(CORPUS_DIR, 'cases.json'), 'utf-8'));
}

export function fixtureHtml(name: string): string {
  return fs.readFileSync(path.join(CORPUS_DIR, 'html', `${name}.html`), 'utf-8');
}

export function hasDirective(url: string): boolean {
  return url.includes(':~:');
}

export function squash(text: string): string {
  return text.replace(/[\s\u200b]+/g, '');
}
