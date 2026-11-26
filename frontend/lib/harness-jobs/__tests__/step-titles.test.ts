import { describe, expect, it } from 'vitest';
import { looksLikeData, plainDetail, stepTitle, toolLabel, toolTitle } from '../step-titles';
import { words } from './fixtures';

describe('plain titles for tool steps', () => {
  it('names a tool in words, without its server prefix', () => {
    expect(toolLabel('goldendocs__search_docs')).toBe('Search docs');
    expect(toolLabel('jira.createIssue')).toBe('Jira create issue');
    expect(toolLabel('x', 'Send a Slack message')).toBe('Send a Slack message');
  });

  it('says what happened to what when the worker sent the step\'s facts', () => {
    expect(stepTitle({ action: 'readPage', target: 'acme.com', title: 'Pricing, Acme' }, words)).toBe('Read Pricing, Acme');
    expect(stepTitle({ action: 'webSearch', target: 'PAY-1423' }, words)).toBe('Searched the web for “PAY-1423”');
    expect(stepTitle({ action: 'listFiles', target: 'src' }, words)).toBe('Listed the files in src');
    expect(stepTitle({ action: 'readFile' }, words)).toBeNull();
  });

  it('never shows JSON or an id as a detail', () => {
    expect(looksLikeData('{"q":"x"}')).toBe(true);
    expect(looksLikeData('run 9d1315d0-07f1-47ef-8880-58bfda705115')).toBe(true);
    expect(looksLikeData('Search Docs: "paid leave"')).toBe(false);
    expect(plainDetail('{"q":"x"}')).toBeUndefined();
    expect(toolTitle({ toolName: 'search', argsSummary: '{"q":"x"}' }, words)).toEqual({ title: 'Used Search', detail: undefined });
    expect(toolTitle({ toolName: 'code_task' }, words)).toEqual({ title: 'Handed the work to a background agent' });
  });
});
