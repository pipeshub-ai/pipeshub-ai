// Domain folders hold pure logic and must not reach into transport or persistence.
export const DOMAIN_FOLDER_GLOBS = ['src/**/domain/**/*.ts'];

export const domainBoundaryConfig = {
  files: DOMAIN_FOLDER_GLOBS,
  rules: {
    'no-restricted-imports': [
      'error',
      {
        paths: [
          { name: 'express', message: 'Domain code must not depend on the HTTP layer.' },
          { name: 'mongoose', message: 'Domain code must not depend on persistence; use a repository interface.' },
        ],
        patterns: [
          { group: ['**/schema/**'], message: 'Domain code must not import Mongoose schemas.' },
          { group: ['**/controller/**'], message: 'Domain code must not import controllers.' },
        ],
      },
    ],
  },
};
