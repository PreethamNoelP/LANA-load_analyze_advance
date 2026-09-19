import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import AuthGate from './components/AuthGate.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './index.css'

// AuthGate sits inside the error boundary and outside the app: it asks the
// backend once whether a token is required and renders nothing at all when it
// is not, which is the default single-user case. Wrapping App rather than
// living inside it means no component below has to know authentication exists.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <AuthGate>
        <App />
      </AuthGate>
    </ErrorBoundary>
  </React.StrictMode>
)