import test from 'node:test';
import * as assert from 'node:assert/strict';
import { mimeTypeForPath, mimeTypeForPathOrUndefined } from '../mime';

test('a .log or .cfg is stamped with its own type, not text/plain', () => {
  assert.equal(mimeTypeForPathOrUndefined('logs/server.log'), 'text/x-log');
  assert.equal(mimeTypeForPathOrUndefined('etc/app.CFG'), 'text/x-cfg');
  assert.equal(mimeTypeForPath('notes.txt'), 'text/plain');
  assert.equal(mimeTypeForPathOrUndefined('a.unknownext'), undefined);
});
