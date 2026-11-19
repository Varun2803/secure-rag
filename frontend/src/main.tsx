import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './app/App'
import { AppErrorBoundary } from './components/shared/app-error-boundary'
import { AppProviders } from './app/providers/app-providers'
import './styles/globals.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <AppProviders>
        <App />
      </AppProviders>
    </AppErrorBoundary>
  </React.StrictMode>,
)
