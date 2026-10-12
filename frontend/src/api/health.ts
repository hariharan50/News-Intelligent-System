export type CheckStatus = 'ok' | 'error'

export interface ReadyResponse {
  status: CheckStatus
  database: CheckStatus
  redis: CheckStatus
  pipeline: {
    status: 'ok' | 'stale' | 'missing'
    last_heartbeat_age_seconds: number | null
  }
}

/** Readiness returns 503 with the same body when a dependency is down, so read the body either way. */
export async function fetchReadiness(signal?: AbortSignal): Promise<ReadyResponse> {
  const res = await fetch('/api/health/ready', { signal })
  if (res.status !== 200 && res.status !== 503) {
    throw new Error(`Unexpected status ${res.status}`)
  }
  return (await res.json()) as ReadyResponse
}
