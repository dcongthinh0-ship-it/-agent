import { useEffect, useState } from 'react'
import { ApiFailure } from '../../api/client'
import { getCatalog, getRegimenBundle, getEvidence } from '../../api/catalog'
import type { EvidenceDetail, EvidenceList, EvidenceSummary, RegimenDetail, RegimenPage, RegimenSummary } from '../../types/contracts'

export function useCatalog() {
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [catalog, setCatalog] = useState<RegimenPage | null>(null)
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [catalogRetry, setCatalogRetry] = useState(0)
  const [selected, setSelected] = useState<RegimenSummary | null>(null)
  const [detail, setDetail] = useState<RegimenDetail | null>(null)
  const [evidence, setEvidence] = useState<EvidenceList | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailRetry, setDetailRetry] = useState(0)
  const [drawerItem, setDrawerItem] = useState<EvidenceSummary | null>(null)
  const [drawerDetail, setDrawerDetail] = useState<EvidenceDetail | null>(null)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [mobileDetail, setMobileDetail] = useState(false)
  const [formMode, setFormMode] = useState<'view' | 'edit' | null>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query.trim()); setPage(1) }, 320)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    const controller = new AbortController()
    setCatalogLoading(true)
    setCatalogError(null)
    setSelected(null)
    setDetail(null)
    setEvidence(null)
    setDrawerItem(null)
    setMobileDetail(false)
    getCatalog(search, page, controller.signal)
      .then(setCatalog)
      .catch(error => { if (error.name !== 'AbortError') setCatalogError(error instanceof ApiFailure ? error.message : '方案目录读取失败') })
      .finally(() => { if (!controller.signal.aborted) setCatalogLoading(false) })
    return () => controller.abort()
  }, [search, page, catalogRetry])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    setDetailLoading(true)
    setDetailError(null)
    setDetail(null)
    setEvidence(null)
    getRegimenBundle(selected.regimen_id, selected.version_id, controller.signal).then(([nextDetail, nextEvidence]) => { setDetail(nextDetail); setEvidence(nextEvidence) })
      .catch(error => { if (error.name !== 'AbortError') setDetailError(error instanceof ApiFailure ? error.message : '固定版本读取失败') })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [selected, detailRetry])

  useEffect(() => {
    if (!drawerItem) return
    const controller = new AbortController()
    setDrawerDetail(null)
    setDrawerError(null)
    getEvidence(drawerItem.evidence_id, controller.signal)
      .then(setDrawerDetail)
      .catch(error => { if (error.name !== 'AbortError') setDrawerError(error instanceof ApiFailure ? error.message : '证据原文读取失败') })
    return () => controller.abort()
  }, [drawerItem])

  const choose = (item: RegimenSummary) => { setSelected(item); setMobileDetail(true); setDrawerItem(null); setFormMode(null) }
  const totalPages = Math.max(1, Math.ceil((catalog?.total ?? 0) / 20))
  return { query, setQuery, search, setSearch, page, setPage, catalog, setCatalog, catalogLoading, setCatalogLoading, catalogError, setCatalogError, catalogRetry, setCatalogRetry, selected, setSelected, detail, setDetail, evidence, setEvidence, detailLoading, setDetailLoading, detailError, setDetailError, detailRetry, setDetailRetry, drawerItem, setDrawerItem, drawerDetail, setDrawerDetail, drawerError, setDrawerError, mobileDetail, setMobileDetail, formMode, setFormMode, choose, totalPages }
}
