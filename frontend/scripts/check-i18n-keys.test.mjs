import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { findUnresolvedKeys } from './check-i18n-keys.mjs';

const dir = mkdtempSync(join(tmpdir(), 'i18n-keys-'));
let seq = 0;
function sourceFile(contents) {
  const path = join(dir, `source-${(seq += 1)}.tsx`);
  writeFileSync(path, contents, 'utf8');
  return path;
}

const catalogue = {
  chat: { send: 'Send', attachments: { tooMany_one: 'One', tooMany_other: 'Many' } },
  common: { docs: 'Docs' },
};

test('accepts keys the catalogue resolves, including through a plural group', () => {
  const file = sourceFile(`
    const a = t('chat.send');
    const b = t("common.docs");
    const c = t('chat.attachments.tooMany', { count: n });
  `);
  assert.deepEqual(findUnresolvedKeys(catalogue, [file]), []);
});

test('reports a key the catalogue does not have, even with a defaultValue', () => {
  const file = sourceFile(`
    const a = t('chat.missing', { defaultValue: 'Missing' });
    const b = t('chat.send');
  `);
  assert.deepEqual(
    findUnresolvedKeys(catalogue, [file]).map((entry) => entry.key),
    ['chat.missing'],
  );
});

test('reads Trans i18nKey in every quoting JSX allows', () => {
  const file = sourceFile(`
    <Trans i18nKey="common.docs" />
    <Trans i18nKey='common.docs' />
    <Trans i18nKey={'chat.absent'} />
    <Trans i18nKey={"chat.alsoAbsent"} />
    <Trans i18nKey='chat.singleQuoted' />
  `);
  assert.deepEqual(
    findUnresolvedKeys(catalogue, [file]).map((entry) => entry.key).sort(),
    ['chat.absent', 'chat.alsoAbsent', 'chat.singleQuoted'],
  );
});

test('ignores interpolated keys and bare identifiers it cannot check', () => {
  const file = sourceFile(`
    const a = t(\`workspace.actions.validation.\${code}\`);
    const b = t(someKey);
    const c = t('Web Search');
  `);
  assert.deepEqual(findUnresolvedKeys(catalogue, [file]), []);
});

test('reports each unresolved key once per file', () => {
  const file = sourceFile(`
    const a = t('chat.gone');
    const b = t('chat.gone');
  `);
  assert.equal(findUnresolvedKeys(catalogue, [file]).length, 1);
});
