/* SourcePicker: the picker is generated, and secrets do not leak into it.
 *
 * The two tests that matter here are the ones asserting properties rather
 * than markup: that the list of sources comes from the API (so a new backend
 * connector appears with no frontend change), and that a typed password is
 * sent in the request but never rendered back into the DOM.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SourcePicker from './SourcePicker.jsx'
import * as api from '../api.js'

vi.mock('../api.js', () => ({
  listSources: vi.fn(),
  testSource: vi.fn(),
  previewSource: vi.fn(),
  loadSource: vi.fn(),
}))

const SOURCES = {
  sources: [
    {
      kind: 'sql',
      description: 'A SQL database.',
      available: true,
      unavailable_reason: '',
      capabilities: {
        lists_entities: true, needs_entity: true, accepts_query: true,
        uses_secret: true, target_label: 'Connection URL',
        target_placeholder: 'postgresql://…', entity_label: 'Table',
      },
    },
    {
      kind: 'mongodb',
      description: 'A MongoDB collection.',
      available: false,
      unavailable_reason: 'pymongo is not installed.',
      capabilities: {
        lists_entities: true, needs_entity: true, accepts_query: true,
        uses_secret: true, target_label: 'Connection URI',
        target_placeholder: 'mongodb://…', entity_label: 'Collection',
      },
    },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listSources.mockResolvedValue(SOURCES)
})

describe('SourcePicker', () => {
  it('renders one card per source returned by the API', async () => {
    render(<SourcePicker onLoaded={() => {}} />)
    await waitFor(() => expect(screen.getByText('sql')).toBeInTheDocument())
    expect(screen.getByText('mongodb')).toBeInTheDocument()
    expect(screen.getByText('A SQL database.')).toBeInTheDocument()
  })

  it('disables a source whose driver is missing and says why', async () => {
    render(<SourcePicker onLoaded={() => {}} />)
    await waitFor(() => expect(screen.getByText('mongodb')).toBeInTheDocument())

    const card = screen.getByText('mongodb').closest('button')
    expect(card).toBeDisabled()
    expect(screen.getByText('pymongo is not installed.')).toBeInTheDocument()
  })

  it('labels the connection field from the backend, not a hardcoded string', async () => {
    const user = userEvent.setup()
    render(<SourcePicker onLoaded={() => {}} />)
    await waitFor(() => expect(screen.getByText('sql')).toBeInTheDocument())

    await user.click(screen.getByText('sql'))
    expect(screen.getByText(/Connection URL/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('postgresql://…')).toBeInTheDocument()
    // Capability-driven: this connector declares accepts_query, so the query
    // box is present. One that does not would not render it.
    expect(screen.getByText(/Query/)).toBeInTheDocument()
  })

  it('offers discovered tables as a dropdown after a successful test', async () => {
    const user = userEvent.setup()
    api.testSource.mockResolvedValue({
      ok: true, detail: 'Connected. 2 tables visible.',
      entities: ['orders', 'customers'], truncated_entities: false,
    })

    render(<SourcePicker onLoaded={() => {}} />)
    await waitFor(() => expect(screen.getByText('sql')).toBeInTheDocument())
    await user.click(screen.getByText('sql'))
    await user.type(screen.getByPlaceholderText('postgresql://…'), 'sqlite:///x.db')
    await user.click(screen.getByRole('button', { name: /Test connection/ }))

    await waitFor(() =>
      expect(screen.getByText('Connected. 2 tables visible.', { exact: false }))
        .toBeInTheDocument())
    expect(screen.getByRole('option', { name: 'orders' })).toBeInTheDocument()
  })

  it('surfaces a failed connection as an error rather than silence', async () => {
    const user = userEvent.setup()
    api.testSource.mockResolvedValue({
      ok: false, detail: 'Could not connect: authentication failed', entities: [],
    })

    render(<SourcePicker onLoaded={() => {}} />)
    await waitFor(() => expect(screen.getByText('sql')).toBeInTheDocument())
    await user.click(screen.getByText('sql'))
    await user.type(screen.getByPlaceholderText('postgresql://…'), 'sqlite:///x.db')
    await user.click(screen.getByRole('button', { name: /Test connection/ }))

    await waitFor(() =>
      expect(screen.getByText(/authentication failed/)).toBeInTheDocument())
  })

  it('sends a typed secret to the API but never renders it back', async () => {
    const user = userEvent.setup()
    api.testSource.mockResolvedValue({ ok: true, detail: 'Connected.', entities: [] })

    const { container } = render(<SourcePicker onLoaded={() => {}} />)
    await waitFor(() => expect(screen.getByText('sql')).toBeInTheDocument())
    await user.click(screen.getByText('sql'))
    await user.type(screen.getByPlaceholderText('postgresql://…'), 'sqlite:///x.db')

    const secretField = container.querySelector('input[type="password"]')
    await user.type(secretField, 'hunter2')
    await user.click(screen.getByRole('button', { name: /Test connection/ }))

    await waitFor(() => expect(api.testSource).toHaveBeenCalled())
    expect(api.testSource.mock.calls[0][0].secret).toBe('hunter2')
    // The value lives in the field (masked) but must not be echoed anywhere
    // else in the rendered output.
    expect(container.textContent).not.toContain('hunter2')
  })

  it('hands the loaded session up unchanged', async () => {
    const user = userEvent.setup()
    const onLoaded = vi.fn()
    api.loadSource.mockResolvedValue({ session_id: 'abc', filename: 'db.orders', rows: 8 })

    render(<SourcePicker onLoaded={onLoaded} />)
    await waitFor(() => expect(screen.getByText('sql')).toBeInTheDocument())
    await user.click(screen.getByText('sql'))
    await user.type(screen.getByPlaceholderText('postgresql://…'), 'sqlite:///x.db')
    await user.type(screen.getByRole('textbox', { name: /Table/ }), 'orders')
    await user.click(screen.getByRole('button', { name: /Load into LANA/ }))

    await waitFor(() => expect(onLoaded).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: 'abc' })))
  })

  it('does not send an empty secret as if one had been given', async () => {
    const user = userEvent.setup()
    api.testSource.mockResolvedValue({ ok: true, detail: 'Connected.', entities: [] })

    render(<SourcePicker onLoaded={() => {}} />)
    await waitFor(() => expect(screen.getByText('sql')).toBeInTheDocument())
    await user.click(screen.getByText('sql'))
    await user.type(screen.getByPlaceholderText('postgresql://…'), 'sqlite:///x.db')
    await user.click(screen.getByRole('button', { name: /Test connection/ }))

    await waitFor(() => expect(api.testSource).toHaveBeenCalled())
    expect(api.testSource.mock.calls[0][0].secret).toBeUndefined()
  })
})
