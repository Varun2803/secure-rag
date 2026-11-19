import { Component } from 'react'
import type { PropsWithChildren, ReactNode } from 'react'
import { ErrorState } from './error-state'

type Props = PropsWithChildren
type State = { hasError: boolean }

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch() {
    // Deliberately avoid logging user or document data from render failures.
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <main className="mx-auto flex min-h-screen max-w-3xl items-center px-6">
          <ErrorState title="The workspace could not be displayed" message="Reload the page to try again." />
        </main>
      )
    }
    return this.props.children
  }
}
