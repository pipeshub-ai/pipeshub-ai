import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const saveTextFile = vi.hoisted(() => vi.fn());
vi.mock('../../../utils/save-text-file', () => ({ saveTextFile }));

import { AnswerContent } from '../answer-content';

// No JSX here, same as answer-content-table-scroll.test.tsx.
const h = React.createElement;

function renderAnswer(content: string) {
  return render(h(Theme, null, h(AnswerContent, { content, isStreaming: false })));
}

const downloadCsvButtons = () => screen.queryAllByRole('button', { name: 'Download as CSV' });
const codeDownloadButtons = () =>
  screen.queryAllByText('Download').map((el) => el.closest('button')).filter((b): b is HTMLButtonElement => b !== null);

beforeEach(() => saveTextFile.mockReset());
afterEach(() => cleanup());

const GFM_TABLE = ['| Name | Qty |', '| --- | --- |', '| Apples | 3 |', '| Pears | 5 |'].join('\n');

describe('downloading a table as CSV', () => {
  it('offers the download on a markdown table and saves its rows', () => {
    renderAnswer(GFM_TABLE);

    fireEvent.click(downloadCsvButtons()[0]);

    expect(saveTextFile).toHaveBeenCalledTimes(1);
    expect(saveTextFile).toHaveBeenCalledWith('table.csv', '\uFEFFName,Qty\r\nApples,3\r\nPears,5\r\n', 'text/csv');
  });

  it('saves only the rows the model wrote, without the source badges beside them', () => {
    renderAnswer(['| Claim | Note |', '| --- | --- |', '| Revenue grew 12% [1] | see a[0] |'].join('\n'));

    fireEvent.click(downloadCsvButtons()[0]);

    expect(saveTextFile.mock.calls[0][1]).toBe('\uFEFFClaim,Note\r\nRevenue grew 12%,see a[0]\r\n');
  });

  it('leaves out a source the answer wrote as a link, but keeps an ordinary link text', () => {
    renderAnswer(
      ['| Claim | Read |', '| --- | --- |', '| Costs fell [2](https://x.test/doc) sharply | [the docs](https://x.test/d) |'].join('\n'),
    );

    fireEvent.click(downloadCsvButtons()[0]);

    expect(saveTextFile.mock.calls[0][1]).toBe('\uFEFFClaim,Read\r\nCosts fell sharply,the docs\r\n');
  });

  it('quotes a cell that has a comma and keeps formatting out of the text', () => {
    renderAnswer(['| Item | Price |', '| --- | --- |', '| **Tea, green** | $3,50 |'].join('\n'));

    fireEvent.click(downloadCsvButtons()[0]);

    expect(saveTextFile.mock.calls[0][1]).toBe('\uFEFFItem,Price\r\n"Tea, green","$3,50"\r\n');
  });

  it('saves a cell that starts like a formula as text', () => {
    renderAnswer(['| A | B |', '| --- | --- |', '| =1+1 | x |'].join('\n'));

    fireEvent.click(downloadCsvButtons()[0]);

    expect(saveTextFile.mock.calls[0][1]).toBe("\uFEFFA,B\r\n'=1+1,x\r\n");
  });

  it('offers the download on a csv block too, with the cells as the model wrote them', () => {
    renderAnswer(['```csv', 'Customer,Fix', 'Northwind,"Per project, then queue"', '```'].join('\n'));

    fireEvent.click(downloadCsvButtons()[0]);

    expect(saveTextFile).toHaveBeenCalledWith(
      'table.csv',
      '\uFEFFCustomer,Fix\r\nNorthwind,"Per project, then queue"\r\n',
      'text/csv',
    );
  });

  it('saves each table its own rows when an answer has two', () => {
    renderAnswer([GFM_TABLE, '', '| City |', '| --- |', '| Oslo |'].join('\n'));

    const buttons = downloadCsvButtons();
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[1]);
    fireEvent.click(buttons[0]);

    expect(saveTextFile.mock.calls[0][1]).toBe('\uFEFFCity\r\nOslo\r\n');
    expect(saveTextFile.mock.calls[1][1]).toBe('\uFEFFName,Qty\r\nApples,3\r\nPears,5\r\n');
  });

  it('adds no download where there is no table', () => {
    renderAnswer('Just a sentence.');

    expect(downloadCsvButtons()).toHaveLength(0);
  });

  it('keeps the expand button first, so it stays the first button on the table', () => {
    const { container } = renderAnswer(GFM_TABLE);

    const titled = Array.from(container.querySelectorAll<HTMLButtonElement>('button[title]'));
    expect(titled.map((b) => b.title)).toEqual(['Expand table', 'Download as CSV']);
  });
});

describe('downloading a code block', () => {
  it('saves the code under a file name for its language', () => {
    renderAnswer('```python\nprint("hi")\n```');

    fireEvent.click(codeDownloadButtons()[0]);

    expect(saveTextFile).toHaveBeenCalledWith('code.py', 'print("hi")\n', 'text/plain');
  });

  it.each([
    ['typescript', 'code.ts'],
    ['bash', 'code.sh'],
    ['json', 'code.json'],
    ['somethingmadeup', 'code.txt'],
  ])('names a %s block %s', (language, fileName) => {
    renderAnswer(`\`\`\`${language}\nx\n\`\`\``);

    fireEvent.click(codeDownloadButtons()[0]);

    expect(saveTextFile.mock.calls[0][0]).toBe(fileName);
  });

  it('saves a block with no language as a text file', () => {
    renderAnswer('```\nplain words\n```');

    fireEvent.click(codeDownloadButtons()[0]);

    expect(saveTextFile).toHaveBeenCalledWith('code.txt', 'plain words\n', 'text/plain');
  });

  it('keeps the indentation and blank lines of the code', () => {
    renderAnswer('```python\ndef f():\n\n    return 1\n```');

    fireEvent.click(codeDownloadButtons()[0]);

    expect(saveTextFile.mock.calls[0][1]).toBe('def f():\n\n    return 1\n');
  });

  it('saves the block that was clicked when an answer has several', () => {
    renderAnswer('```python\na = 1\n```\n\n```sql\nselect 1;\n```');

    const buttons = codeDownloadButtons();
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[1]);

    expect(saveTextFile).toHaveBeenCalledWith('code.sql', 'select 1;\n', 'text/plain');
  });

  it('keeps the copy button beside the download button', () => {
    renderAnswer('```python\nx = 1\n```');

    expect(screen.getAllByText('Copy')).toHaveLength(1);
    expect(codeDownloadButtons()).toHaveLength(1);
  });

  it('does not download a csv block as code, since it shows as a table', () => {
    renderAnswer('```csv\na,b\n1,2\n```');

    expect(codeDownloadButtons()).toHaveLength(0);
    expect(downloadCsvButtons()).toHaveLength(1);
  });
});
