import { expect } from 'chai';
import {
  artifactIdParamsSchema,
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
});
