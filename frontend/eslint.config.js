import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

// Deliberately close to the defaults. The value here is catching the class of
// bug this codebase can actually produce — a wrong hook dependency array, a
// stale closure, an unused binding hiding a rename that was never finished —
// not enforcing a house style nobody asked for.
export default [
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      globals: { ...globals.browser, ...globals.es2021 },
      parserOptions: {
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // Unused args are common and harmless in event handlers; unused
      // variables are usually a half-finished edit.
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],

      // Downgraded deliberately, not to make the build green. LANA fetches in
      // effects because it has no data-fetching library and doesn't need one
      // at this size; "setLoading(true) then await" is the whole pattern. The
      // rule's concern is extra renders under the React Compiler, not
      // correctness, so it stays visible as a warning rather than either
      // failing CI on working code or forcing a data-layer rewrite nobody
      // asked for.
      'react-hooks/set-state-in-effect': 'warn',
      // Genuinely useful, but it cannot see that a locally-defined async
      // function is safe to omit; left as a warning so the real cases still
      // get read rather than being silenced file-by-file.
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
  {
    files: ['**/*.test.{js,jsx}', 'vitest.setup.js'],
    languageOptions: { globals: { ...globals.node } },
  },
]
