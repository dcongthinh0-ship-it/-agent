import { getJson, postJson } from './client'
import type { AgentReadout } from '../types/contracts'

export function listAgentRuns(contextId: string, params: URLSearchParams, signal?: AbortSignal) {
  return getJson<{ items: { id: string }[] }>(`/api/v1/contexts/${contextId}/agent-runs?${params}`, signal)
}

export function readAgentRun(contextId: string, runId: string, signal?: AbortSignal) {
  return getJson<AgentReadout>(`/api/v1/contexts/${contextId}/agent-runs/${runId}`, signal)
}

export function startAgentRun(contextId: string, payload: unknown, key: string) {
  return postJson<{ agent_run_id: string }>(`/api/v1/contexts/${contextId}/agent-runs`, payload, key)
}
