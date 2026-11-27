import type { paths } from './api'

type Health = paths['/api/v1/health']['get']['responses'][200]['content']['application/json']

export async function fetchHealth(): Promise<Health> {
  const response = await fetch('/api/v1/health')
  if (!response.ok) {
    throw new Error(`Health request failed (${response.status})`)
  }
  return response.json()
}
