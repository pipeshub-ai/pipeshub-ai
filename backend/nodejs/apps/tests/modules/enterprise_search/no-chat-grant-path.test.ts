import { expect } from 'chai';
import { readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';

const SRC = join(__dirname, '../../../src');

const sources = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return sources(full);
    return full.endsWith('.ts') ? [full] : [];
  });

describe('PR-7.3: no Node code path grants chat-content access', () => {
  const files = sources(SRC);

  it('never calls the removed Python grant routes or helpers', () => {
    const banned = [
      /chat\/(attachments|artifacts)\/permissions/,
      /syncConversationRecordPermissions/,
      /collectChatAttachmentRecordIds/,
      /loadConversationChatAttachmentRecordIds/,
    ];
    const hits = files.flatMap((f) => {
      const text = readFileSync(f, 'utf8');
      return banned.filter((re) => re.test(text)).map((re) => `${f}: ${String(re)}`);
    });
    expect(hits).to.deep.equal([]);
  });

  it('uses the conversation:permissions scope only to validate attachments', () => {
    const users = files.filter((f) => readFileSync(f, 'utf8').includes('CONVERSATION_PERMISSIONS'));
    expect(users.map((f) => f.slice(SRC.length + 1)).sort()).to.deep.equal([
      'libs/enums/token-scopes.enum.ts',
      'modules/enterprise_search/utils/attachment-validation.ts',
    ]);
  });
});
