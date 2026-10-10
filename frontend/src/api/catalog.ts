import { getJson } from './client'
import type { EvidenceDetail, EvidenceList, RegimenDetail, RegimenPage } from '../types/contracts'

export function getCatalog(search: string, page: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ search, page: String(page), page_size: '20' })
  return getJson<RegimenPage>(`/api/v1/regimens?${params}`, signal)
}

export function getRegimenBundle(regimenId: string, versionId: string, signal?: AbortSignal) {
  const base = `/api/v1/regimens/${regimenId}/versions/${versionId}`
  return Promise.all([
    getJson<RegimenDetail>(base, signal),
    getJson<EvidenceList>(`${base}/evidence`, signal),
  ])
}

export function getEvidence(evidenceId: string, signal?: AbortSignal) {
  return getJson<EvidenceDetail>(`/api/v1/evidence/${evidenceId}`, signal)
}
