import 'reflect-metadata';
import { expect } from 'chai';
import { Types } from 'mongoose';
import { isKnowledgeLimitWithin } from '../../../../src/modules/oauth_provider/schema/knowledge_limit.schema';
import { OAuthRefreshToken } from '../../../../src/modules/oauth_provider/schema/oauth.refresh_token.schema';

const limit = (apps: string[], recordGroups: string[] = [], records: string[] = []) => ({
  apps,
  recordGroups,
  records,
});

describe('isKnowledgeLimitWithin', () => {
  it('holds the same limit, in any order', () => {
    expect(isKnowledgeLimitWithin(limit(['a', 'b'], [], ['f1']), limit(['b', 'a'], [], ['f1']))).to.equal(true);
  });

  it('holds a limit inside one that names more', () => {
    expect(isKnowledgeLimitWithin(limit(['a']), limit(['a', 'b'], ['g1'], ['f1']))).to.equal(true);
  });

  it('does not hold a limit that names an item the outer one dropped', () => {
    expect(isKnowledgeLimitWithin(limit(['a', 'b']), limit(['a']))).to.equal(false);
    expect(isKnowledgeLimitWithin(limit([], ['g1']), limit([], []))).to.equal(false);
    expect(isKnowledgeLimitWithin(limit([], [], ['f1']), limit(['a']))).to.equal(false);
  });

  it('treats no limit as everything', () => {
    expect(isKnowledgeLimitWithin(limit(['a']), undefined)).to.equal(true);
    expect(isKnowledgeLimitWithin(undefined, null)).to.equal(true);
    expect(isKnowledgeLimitWithin(undefined, limit(['a']))).to.equal(false);
    expect(isKnowledgeLimitWithin(null, limit(['a']))).to.equal(false);
  });

  it('holds a limit that names nothing inside any limit', () => {
    expect(isKnowledgeLimitWithin(limit([]), limit([]))).to.equal(true);
  });
});

describe('the knowledge limit stored on a refresh token', () => {
  const token = (knowledgeLimit?: unknown) =>
    new OAuthRefreshToken({
      tokenHash: 'h',
      clientId: 'c',
      userId: new Types.ObjectId(),
      orgId: new Types.ObjectId(),
      scopes: ['offline_access'],
      expiresAt: new Date(),
      ...(knowledgeLimit === undefined ? {} : { knowledgeLimit }),
    }).toObject();

  // A refresh tells "no limit" from "stored before the limit was kept" by
  // null against absent, so both have to survive the schema as they are.
  it('keeps null for no limit and leaves the field absent when it is not given', () => {
    expect(token(null)).to.have.property('knowledgeLimit', null);
    expect(token()).not.to.have.property('knowledgeLimit');
  });

  it('keeps a limit as given', () => {
    expect(token(limit(['a'], [], ['f1'])).knowledgeLimit).to.deep.equal(limit(['a'], [], ['f1']));
  });
});
