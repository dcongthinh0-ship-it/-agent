import { getJson, postJson } from './client'
import type { CandidateDetail, ContextReadout } from '../types/contracts'

export function readContext(contextId: string, signal?: AbortSignal) {
  return getJson<ContextReadout>(`/api/v1/contexts/${contextId}`, signal)
}

export function readCandidate(contextId: string, candidateId: string, signal?: AbortSignal) {
  return getJson<CandidateDetail>(`/api/v1/contexts/${contextId}/candidates/${candidateId}`, signal)
}

export function readCandidateEvidence<T>(contextId: string, candidateId: string, signal?: AbortSignal) {
  return getJson<T>(`/api/v1/contexts/${contextId}/candidates/${candidateId}/evidence`, signal)
}

export function recordCandidateView(contextId: string, candidateId: string) {
  return postJson(`/api/v1/contexts/${contextId}/actions`, { kind: 'VIEW', candidate_ids: [candidateId] })
}

export type WorkstationCommand = 'refresh' | 'select' | 'compare'

export function sendWorkstationCommand<T>(contextId: string, operation: WorkstationCommand, body: unknown, key: string, candidateId?: string) {
  const base = `/api/v1/contexts/${contextId}`
  const path = operation === 'select' ? `${base}/candidates/${candidateId}/select`
    : operation === 'compare' ? `${base}/actions` : `${base}/refresh`
  return postJson<T>(path, body, key)
}
