import { expect } from 'chai';
import {
  artifactPathSchema,
  ArtifactFileParams,
  ArtifactReadUrlsSchema,
  PresignArtifactsSchema,
} from '../../../../src/modules/storage/validators/validators';

// properties.json is accepted as an artifact file by its exact name only.
describe('artifact properties.json path', () => {
  const headers = { authorization: 'Bearer t' };
  const LOOKALIKES = [
    'Properties.json', 'properties.JSON', 'PROPERTIES.JSON', 'properties.json/', '/properties.json',
    './properties.json', '../properties.json', 'x/../properties.json', 'properties.json/..',
    'properties.json.', 'properties.jsonl', 'properties.json ', ' properties.json', 'properties.json\n',
    '\nproperties.json', 'properties.json\r', 'properties.json\\', '\\properties.json', 'properties\\.json',
    'properties.json\0', 'properties%2ejson', 'properties.json%00', 'enrich/properties.json',
    'blocks/properties.json', 'propertiesXjson', 'properties.jso', 'roperties.json', 'properties.json.json',
    'propertieſ.json', 'properties.ĵson', 'ｐroperties.json', 'properties​.json',
    'properties.json\u0000x', '..\\properties.json', 'properties.json?x=1', 'properties.json#a',
    '',
  ];

  it('accepts the exact name', () => {
    expect(artifactPathSchema.safeParse('properties.json').success).to.equal(true);
  });

  LOOKALIKES.forEach((path) => {
    it(`refuses ${JSON.stringify(path)}`, () => {
      expect(artifactPathSchema.safeParse(path).success).to.equal(false);
    });
  });

  it('refuses a whole presign that names one lookalike beside valid files', () => {
    const body = {
      virtualRecordId: 'vr-1', rev: 'abcdef12',
      files: [
        { path: 'record.json', contentType: 'application/json' },
        { path: 'properties.json', contentType: 'application/json' },
        { path: 'Properties.json', contentType: 'application/json' },
      ],
    };
    expect(PresignArtifactsSchema.safeParse({ headers, body }).success).to.equal(false);
    body.files.pop();
    expect(PresignArtifactsSchema.safeParse({ headers, body }).success).to.equal(true);
  });

  it('refuses properties.json twice in one presign', () => {
    const body = {
      virtualRecordId: 'vr-1', rev: 'abcdef12',
      files: [
        { path: 'properties.json', contentType: 'application/json' },
        { path: 'properties.json', contentType: 'application/json' },
      ],
    };
    expect(PresignArtifactsSchema.safeParse({ headers, body }).success).to.equal(false);
  });

  it('applies to read-urls and the file routes', () => {
    const documentId = '6adcffdf37cb7c1349fc72e8';
    expect(ArtifactReadUrlsSchema.safeParse({
      headers, params: { documentId }, body: { paths: ['properties.json'] },
    }).success).to.equal(true);
    expect(ArtifactReadUrlsSchema.safeParse({
      headers, params: { documentId }, body: { paths: ['../properties.json'] },
    }).success).to.equal(false);
    expect(ArtifactFileParams.safeParse({
      headers, params: { documentId, 0: 'properties.json' },
    }).success).to.equal(true);
    expect(ArtifactFileParams.safeParse({
      headers, params: { documentId, 0: 'x/../properties.json' },
    }).success).to.equal(false);
  });
});
