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

  // Ignore patterns
  {
    ignores: ['dist/**', 'node_modules/**', '**/*.js', '**/*.mjs'],
  },
);
