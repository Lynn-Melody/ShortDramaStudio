import '@xyflow/react/dist/base.css'
import './styles.css'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'
import { App } from './App'

const root = document.getElementById('root')
if (!root) throw new Error('Missing application root')

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } })

createRoot(root).render(
  <QueryClientProvider client={client}>
    <App />
  </QueryClientProvider>,
)
