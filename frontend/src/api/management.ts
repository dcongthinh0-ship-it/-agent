import { getJson } from './client'

export function readOverview<T>(area: 'evidence' | 'operations', signal?: AbortSignal) {
  return getJson<T>(`/api/v1/${area === 'evidence' ? 'knowledge' : 'operations'}/overview`, signal)
}
