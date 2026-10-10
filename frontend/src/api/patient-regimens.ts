import { getJson, postJson } from './client'
import type { PatientInstance } from '../types/contracts'

function instancePath(contextId: string, instanceId: string) {
  return `/api/v1/contexts/${contextId}/instances/${instanceId}`
}

export function readInstance(contextId: string, instanceId: string) {
  return getJson<PatientInstance>(instancePath(contextId, instanceId))
}

export function readRevision(contextId: string, instanceId: string, revisionId: string) {
  return getJson<PatientInstance>(`${instancePath(contextId, instanceId)}/revisions/${revisionId}`)
}

export function readHospitalReadiness<T>(contextId: string, instanceId: string, signal?: AbortSignal) {
  return getJson<T>(`${instancePath(contextId, instanceId)}/hospital-readiness`, signal)
}

export function saveRevision(contextId: string, instanceId: string, body: unknown, key: string) {
  return postJson(`${instancePath(contextId, instanceId)}/revisions`, body, key)
}

export function confirmRevision(contextId: string, instanceId: string, body: unknown, key: string) {
  return postJson<{ reviewer_status: string; reviewer_run_id?: string; reviewer_error_code?: string }>(`${instancePath(contextId, instanceId)}/confirm`, body, key)
}
