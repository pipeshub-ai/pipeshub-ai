import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import eslintPluginPrettier from 'eslint-plugin-prettier/recommended';

export default tseslint.config(
  // Base recommended rules
  eslint.configs.recommended,

  // Strict type-checked rules (compiled-language strictness)
  ...tseslint.configs.strictTypeChecked,

  // Prettier (must be last to override formatting rules)
  eslintPluginPrettier,

  // Global settings
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  // Rules for all TypeScript files
  {
    files: ['src/**/*.ts'],
    rules: {
      'prettier/prettier': 'error',

      // --- Return type enforcement (like compiled languages) ---
      '@typescript-eslint/explicit-function-return-type': [
        'error',
        {
          allowExpressions: true,
          allowTypedFunctionExpressions: true,
          allowHigherOrderFunctions: true,
          allowDirectConstAssertionInArrowFunctions: true,
        },
      ],
      '@typescript-eslint/explicit-module-boundary-types': 'error',

      // --- Exhaustiveness (like Rust match) ---
      '@typescript-eslint/switch-exhaustiveness-check': 'error',

      // --- Type assertion control (no unsafe object literal casts) ---
      '@typescript-eslint/consistent-type-assertions': [
        'error',
        {
          assertionStyle: 'as',
          objectLiteralTypeAssertions: 'never',
        },
      ],

      // --- Boolean strictness (like Go — no truthy/falsy implicit coercion) ---
      '@typescript-eslint/strict-boolean-expressions': [
        'warn',
        {
          allowString: false,
          allowNumber: false,
          allowNullableObject: true,
          allowNullableBoolean: true,
          allowNullableString: false,
          allowNullableNumber: false,
          allowAny: false,
        },
      ],

      // --- Gradual adoption (warn, not error) ---
      '@typescript-eslint/restrict-template-expressions': 'warn',
      '@typescript-eslint/restrict-plus-operands': 'warn',
      '@typescript-eslint/no-confusing-void-expression': 'warn',
      '@typescript-eslint/prefer-nullish-coalescing': 'warn',
      '@typescript-eslint/prefer-optional-chain': 'warn',

      // --- Keep explicit types — we WANT them (compiled-language style) ---
      '@typescript-eslint/no-inferrable-types': 'off',

      // --- inversify DI requires classes decorated with @injectable() ---
      '@typescript-eslint/no-extraneous-class': 'off',

      // --- Dead code & unused symbols ---
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          args: 'after-used',
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      'no-unreachable': 'error',
    },
  },

  // Tests stub and fake with `any`, assert with chai expressions and keep their own formatting; the rules that
  // catch real mistakes (unused symbols, floating promises, misused promises) still apply.
  {
    files: ['tests/**/*.ts'],
    rules: {
      'prettier/prettier': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unsafe-argument': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-return': 'off',
      '@typescript-eslint/no-unused-expressions': 'off',
      '@typescript-eslint/require-await': 'off',
      '@typescript-eslint/no-non-null-assertion': 'off',
      '@typescript-eslint/unbound-method': 'off',
      '@typescript-eslint/no-confusing-void-expression': 'off',
      '@typescript-eslint/no-require-imports': 'off',
      '@typescript-eslint/no-dynamic-delete': 'off',
      // Style rules that flag how tests log, build messages and stub: not worth rewriting every catch callback and template.
      '@typescript-eslint/use-unknown-in-catch-callback-variable': 'off',
      '@typescript-eslint/restrict-template-expressions': 'off',
      '@typescript-eslint/no-unnecessary-condition': 'off',
      '@typescript-eslint/no-unnecessary-type-assertion': 'off',
      '@typescript-eslint/no-unsafe-enum-assignment': 'off',
    },
  },

  // sharp's loader allowlist is applied when image.utils is imported.
  // Every other file must go through that module so a new decode site
  // cannot skip it.
  {
    files: ['src/**/*.ts'],
    ignores: ['src/libs/utils/image.utils.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'sharp',
              message:
                'Import sharp from src/libs/utils/image.utils so decoding stays limited to JPEG, PNG, WebP and GIF.',
            },
          ],
        },
      ],
    },
  },

  // Ignore patterns
  {
    ignores: ['dist/**', 'node_modules/**', '**/*.js', '**/*.mjs'],
  },
);
