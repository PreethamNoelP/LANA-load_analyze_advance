import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import AskAI from './AskAI.jsx'

// LANA's central claim is that a figure in an answer was computed from the
// user's rows rather than recalled by a language model. The backend earns
// that: it plans a SQL query, runs it in a sandbox, answers from the result
// table, and streams the statement and the table alongside the prose.
//
// The interface used to discard both, which left the claim unverifiable by
// the only person it is addressed to. These tests pin the disclosure itself —
// that the path is labelled, that the statement can be read, and that the
// result the answer was built from is on screen — because a trust signal that
// silently stops rendering is worse than one that was never there.

vi.mock('../api.js', () => ({
  getValidatorCapabilities: vi.fn(() => new Promise(() => {})),
}))

const SQL = {
  sql: 'SELECT "region", AVG("revenue") AS avg_revenue FROM dataset GROUP BY "region"',
  columns: ['region', 'avg_revenue'],
  rows: [['north', 200], ['south', 300]],
  row_count: 2,
  truncated: false,
  elapsed_ms: 12.4,
  attempts: 1,
}

function message(overrides = {}) {
  return {
    id: 1,
    q: 'average revenue by region?',
    a: 'North averages 200 and south averages 300.',
    loading: false,
    ...overrides,
  }
}

describe('AskAI provenance', () => {
  it('labels an executed-query answer as computed', () => {
    render(<AskAI messages={[message({ sql: SQL, grounding: 'sql' })]} />)
    expect(screen.getByText(/Computed by query/)).toBeInTheDocument()
  })

  it('labels a fact-ledger answer differently', () => {
    render(<AskAI messages={[message({ grounding: 'ledger' })]} />)
    expect(screen.getByText(/From computed summary/)).toBeInTheDocument()
    // Nothing to open: no query ran, so offering to show one would be a lie.
    expect(screen.queryByRole('button', { name: /Show the query/ })).toBeNull()
  })

  it('keeps the query collapsed until asked for', () => {
    render(<AskAI messages={[message({ sql: SQL, grounding: 'sql' })]} />)
    expect(screen.queryByText(SQL.sql)).toBeNull()
    expect(screen.getByRole('button', { name: /Show the query/ })).toBeInTheDocument()
  })

  it('reveals the exact statement that produced the figures', async () => {
    const user = userEvent.setup()
    render(<AskAI messages={[message({ sql: SQL, grounding: 'sql' })]} />)

    await user.click(screen.getByRole('button', { name: /Show the query/ }))

    // Verbatim, so the user can re-run it themselves — the strongest form of
    // "you do not have to take our word for it".
    expect(screen.getByText(SQL.sql)).toBeInTheDocument()
  })

  it('shows the result table the answer was built from', async () => {
    const user = userEvent.setup()
    render(<AskAI messages={[message({ sql: SQL, grounding: 'sql' })]} />)
    await user.click(screen.getByRole('button', { name: /Show the query/ }))

    expect(screen.getByRole('columnheader', { name: 'avg_revenue' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'north' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '300' })).toBeInTheDocument()
  })

  it('says so when the result was capped', async () => {
    const user = userEvent.setup()
    render(<AskAI messages={[
      message({ sql: { ...SQL, truncated: true }, grounding: 'sql' }),
    ]} />)
    await user.click(screen.getByRole('button', { name: /Show the query/ }))

    expect(screen.getByText(/capped/)).toBeInTheDocument()
  })

  it('renders nothing at all when no grounding was reported', () => {
    render(<AskAI messages={[message()]} />)
    expect(screen.queryByText(/Computed by query/)).toBeNull()
    expect(screen.queryByText(/From computed summary/)).toBeNull()
  })

  it('still shows the unverified-figure warning alongside provenance', () => {
    render(<AskAI messages={[message({
      sql: SQL,
      grounding: 'sql',
      validation: {
        warnings: ['"450" matches no computed statistic.'],
        verified: 1, numbers_checked: 2, unsupported: 1, misattributed: 0,
      },
    })]} />)

    expect(screen.getByText(/Unverified figures/)).toBeInTheDocument()
    expect(screen.getByText(/Computed by query/)).toBeInTheDocument()
  })
})

describe('AskAI result formatting', () => {
  // The table exists so a reader can check the answer's figures against the
  // query's own output. A raw 267.5210191082802 beside a stated "267.52"
  // makes that comparison harder, not easier.
  const rows = [['north', 267.5210191082802, 157], ['tiny', 0.0032451, 1]]
  const msg = {
    id: 1, q: 'q', a: 'a', loading: false, grounding: 'sql',
    sql: { sql: 'SELECT 1', columns: ['k', 'avg', 'n'], rows, row_count: 2,
           truncated: false, elapsed_ms: 3, attempts: 1 },
  }

  async function open() {
    const user = userEvent.setup()
    render(<AskAI messages={[msg]} />)
    await user.click(screen.getByRole('button', { name: /Show the query/ }))
  }

  it('rounds a long float to something readable', async () => {
    await open()
    expect(screen.getByRole('cell', { name: '267.52' })).toBeInTheDocument()
  })

  it('leaves an integer as an integer', async () => {
    await open()
    // A count of 157 orders is not 157.00.
    expect(screen.getByRole('cell', { name: '157' })).toBeInTheDocument()
  })

  it('keeps precision for values that would round to zero', async () => {
    await open()
    // A correlation or p-value shown as "0.00" has lost everything it said.
    expect(screen.getByRole('cell', { name: '0.003245' })).toBeInTheDocument()
  })

  it('shows a null as a dash rather than the word null', async () => {
    const user = userEvent.setup()
    render(<AskAI messages={[{ ...msg, sql: { ...msg.sql, rows: [['x', null, 1]] } }]} />)
    await user.click(screen.getByRole('button', { name: /Show the query/ }))
    expect(screen.getByRole('cell', { name: '—' })).toBeInTheDocument()
  })
})
