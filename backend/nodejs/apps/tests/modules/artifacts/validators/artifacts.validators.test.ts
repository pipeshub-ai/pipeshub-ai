import { expect } from 'chai';
import {
  artifactIdParamsSchema,
  artifactShareSchema,
  listArtifactsSchema,
} from '../../../../src/modules/artifacts/validators/artifacts.validators';

describe('artifacts validators', () => {
  it('accepts default gallery list query', () => {
    const parsed = listArtifactsSchema.parse({
      query: {},
    });
    expect(parsed.query).to.deep.equal({});
  });

  it('rejects TOOL_RESULT in artifactTypes', () => {
    expect(() =>
      listArtifactsSchema.parse({
        query: { artifactTypes: 'IMAGE,TOOL_RESULT' },
      }),
    ).to.throw();
  });

  it('accepts OTHER, the gallery "Other" filter', () => {
    const parsed = listArtifactsSchema.parse({
      query: { artifactTypes: 'OTHER' },
    });
    expect(parsed.query.artifactTypes).to.equal('OTHER');
  });

  it('accepts a known artifact type list', () => {
    const parsed = listArtifactsSchema.parse({
      query: { artifactTypes: 'IMAGE,CHART', sortBy: 'name', sortOrder: 'asc' },
    });
    expect(parsed.query.artifactTypes).to.equal('IMAGE,CHART');
  });

  it('requires artifactId', () => {
    expect(() =>
      artifactIdParamsSchema.parse({ params: { artifactId: '' } }),
    ).to.throw();
    const parsed = artifactIdParamsSchema.parse({
      params: { artifactId: 'art-1' },
    });
    expect(parsed.params.artifactId).to.equal('art-1');
  });

  it('rejects path separators and traversal segments', () => {
    expect(() =>
      artifactIdParamsSchema.parse({
        params: { artifactId: '../knowledgeBase' },
      }),
    ).to.throw();
    expect(() =>
      artifactIdParamsSchema.parse({ params: { artifactId: 'foo/bar' } }),
    ).to.throw();
    expect(() =>
      artifactIdParamsSchema.parse({ params: { artifactId: 'foo\\bar' } }),
    ).to.throw();
  });

  it('accepts the three gallery tabs and nothing else', () => {
    for (const ownership of ['all', 'mine', 'shared']) {
      expect(
        listArtifactsSchema.parse({ query: { ownership } }).query.ownership,
      ).to.equal(ownership);
    }
    expect(() =>
      listArtifactsSchema.parse({ query: { ownership: 'theirs' } }),
    ).to.throw();
  });

  describe('artifactShareSchema', () => {
    const params = { artifactId: 'art-1' };
    const userId = '507f1f77bcf86cd799439021';

    it('accepts user IDs', () => {
      const parsed = artifactShareSchema.parse({
        params,
        body: { userIds: [userId] },
      });
      expect(parsed.body.userIds).to.deep.equal([userId]);
    });

    it('rejects an empty list, too many users and non-ID values', () => {
      for (const userIds of [
        [],
        Array.from({ length: 101 }, () => userId),
        ['not-a-user-id'],
        [{ $ne: null }],
      ]) {
        expect(() =>
          artifactShareSchema.parse({ params, body: { userIds } }),
        ).to.throw();
      }
    });

    it('rejects an unsafe artifactId', () => {
      expect(() =>
        artifactShareSchema.parse({
          params: { artifactId: '../x' },
          body: { userIds: [userId] },
        }),
      ).to.throw();
    });
  });
});
