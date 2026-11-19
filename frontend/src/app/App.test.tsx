import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { App } from './App'
import { AppProviders } from './providers/app-providers'

function renderApp() {
  return render(<AppProviders><App /></AppProviders>)
}

describe('application foundation', () => {
  it('renders the SecureRAG workspace shell without exposing unfinished feature screens', () => {
    renderApp()

    expect(screen.getAllByText('SecureRAG').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'A secure workspace, ready to grow.' })).toBeInTheDocument()
    expect(screen.getByText('Workspace modules will appear here when they are implemented.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /documents/i })).not.toBeInTheDocument()
  })

  it('switches the accessible theme control between light and dark modes', () => {
    renderApp()
    const themeButton = screen.getAllByRole('button', { name: /switch to dark theme/i })[0]
    expect(themeButton).toBeDefined()

    fireEvent.click(themeButton!)

    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: /switch to light theme/i })).toBeInTheDocument()
  })
})
