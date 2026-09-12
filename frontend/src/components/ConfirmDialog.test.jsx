import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import ConfirmDialog from './ConfirmDialog.jsx'

// This dialog is the control standing between a user and data loss (starting
// over on a dataset, deleting rows during cleaning). A custom-styled modal
// only earns that job if it behaves like one for someone not using a mouse,
// so the behaviour is pinned here rather than left to manual checking.
function setup(props = {}) {
  const onConfirm = vi.fn()
  const onCancel = vi.fn()
  render(
    <ConfirmDialog
      open
      title="Delete rows?"
      message="This removes 20 rows."
      confirmLabel="Delete"
      cancelLabel="Cancel"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  )
  return { onConfirm, onCancel }
}

describe('ConfirmDialog', () => {
  it('renders nothing when closed', () => {
    render(<ConfirmDialog open={false} title="Delete rows?" message="x" />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('announces itself as a modal dialog labelled by its own title', () => {
    setup()
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName('Delete rows?')
    expect(dialog).toHaveAccessibleDescription('This removes 20 rows.')
  })

  it('puts initial focus on Cancel, not on the destructive action', async () => {
    setup()
    // A reflexive Enter on an unexpected dialog should do the safe thing.
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const { onCancel, onConfirm } = setup()

    await user.keyboard('{Escape}')

    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('keeps Tab inside the dialog instead of letting it reach the page behind', async () => {
    const user = userEvent.setup()
    setup()
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    const confirm = screen.getByRole('button', { name: 'Delete' })

    expect(cancel).toHaveFocus()
    await user.tab()
    expect(confirm).toHaveFocus()
    // Wrapping forward from the last control returns to the first, rather
    // than walking out to the obscured controls underneath the overlay.
    await user.tab()
    expect(cancel).toHaveFocus()
    await user.tab({ shift: true })
    expect(confirm).toHaveFocus()
  })

  it('confirms and cancels through their buttons', async () => {
    const user = userEvent.setup()
    const { onConfirm, onCancel } = setup()

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
