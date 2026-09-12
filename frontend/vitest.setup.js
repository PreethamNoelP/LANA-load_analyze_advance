import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// React Testing Library does not unmount between tests on its own here
// (globals mode without its auto-cleanup entry point), and a left-over tree
// keeps document-level listeners alive — exactly the state that makes a
// focus-trap or keydown test pass or fail depending on what ran before it.
afterEach(cleanup)
