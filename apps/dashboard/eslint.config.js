import js from '@eslint/js';
import ts from 'typescript-eslint';
import vue from 'eslint-plugin-vue';
import prettier from 'eslint-config-prettier';

export default ts.config(
  { ignores: ['dist', 'node_modules', 'src/**/*.js', 'src/**/*.d.ts', '!src/env.d.ts'] },
  js.configs.recommended,
  ...ts.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: ts.parser,
      },
    },
  },
  {
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'vue/multi-word-component-names': 'off',
      // A directive-less <template> compiles to an inert native element and
      // silently hides its children (this bug emptied Trends/Breakdowns).
      'vue/no-lone-template': 'error',
    },
  },
  // Prettier owns formatting: disable all ESLint stylistic rules that overlap.
  prettier
);
