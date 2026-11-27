import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'
import { fetchHealth } from './health'

function Studio() {
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, retry: false })
  return (
    <main>
      <h1>短剧工作室</h1>
      <p role="status">
        {health.isPending ? '正在连接 API' : health.data?.status === 'ok' ? 'API 已连接' : 'API 暂不可用'}
      </p>
    </main>
  )
}

const root = document.getElementById('root')
if (!root) throw new Error('Missing application root')

createRoot(root).render(
  <QueryClientProvider client={new QueryClient()}>
    <Studio />
  </QueryClientProvider>,
)
