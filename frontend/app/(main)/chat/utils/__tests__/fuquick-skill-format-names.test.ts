/**
 * FU-QUICK: the built-in packs named by a file extension read as their format's proper name, translated, in the
 * timeline and in the agent builder; "docx" title-cased read "Docx".
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { skillDisplayName, skillReadLabel } from '../tool-display';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];
const FORMATS = ['docx', 'xlsx', 'pptx'];

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('skill format names', () => {
  it('names the office packs by their format in English', () => {
    expect(skillDisplayName('docx')).toBe('Word document');
    expect(skillDisplayName('xlsx')).toBe('Excel spreadsheet');
    expect(skillDisplayName('pptx')).toBe('PowerPoint presentation');
    expect(skillDisplayName('plugin:XLSX')).toBe('Excel spreadsheet');
    expect(skillReadLabel(['pdf', 'docx'])).toBe('Read the PDF and Word document skills');
  });

  it('keeps every other skill in words', () => {
    expect(skillDisplayName('pdf')).toBe('PDF');
    expect(skillDisplayName('data-analysis')).toBe('Data Analysis');
    expect(skillDisplayName('docx-tools')).toBe('Docx Tools');
  });

  it('has a translated name in all ten locales, never the extension', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      for (const format of FORMATS) {
        const name = skillDisplayName(format);
        expect(i18n.exists(`agentActivity.skill.names.${format}`, { lng: locale, fallbackLng: [] }), `${locale} ${format}`).toBe(true);
        expect(name.toLowerCase(), `${locale} ${format}`).not.toContain(format);
        expect(name).toMatch(/Word|Excel|PowerPoint/);
      }
    }
  });
});
