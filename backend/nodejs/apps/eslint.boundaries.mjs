// ADR-007: domain folders hold pure logic and must not reach into transport or persistence.
export const DOMAIN_FOLDER_GLOBS = [
  'src/modules/authz/domain/**/*.ts',
  'src/modules/enterprise_search/services/collaboration/domain/**/*.ts',
];

export const domainBoundaryConfig = {
  files: DOMAIN_FOLDER_GLOBS,
  rules: {
    'no-restricted-imports': [
      'error',
      {
        paths: [
          { name: 'express', message: 'Domain code must not depend on the HTTP layer (ADR-007).' },
          { name: 'mongoose', message: 'Domain code must not depend on persistence; use a repository interface (ADR-007).' },
        ],
        patterns: [
          { group: ['**/schema/**'], message: 'Domain code must not import Mongoose schemas (ADR-007).' },
          { group: ['**/controller/**'], message: 'Domain code must not import controllers (ADR-007).' },
        ],
      },
    ],
  },
};
