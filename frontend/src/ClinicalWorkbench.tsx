import { useEffect, useRef, useState } from 'react'
import { ApiFailure, getJson, postJson, type CandidateDetail, type ContextReadout, type Finding, type PatientInstance, type PreparedCandidate, type SourceReference } from './api'
import { AgentPanel } from './AgentPanel'
import { PatientEditor } from './PatientEditor'
import './clinical-workbench.css'

const message = (error: unknown) => error instanceof ApiFailure ? error.message : '本次操作未完成，请重新读取'
const pending = (value: ContextReadout) => ['QUEUED', 'RUNNING'].includes(value.prepare_status || '') || ['QUEUED', 'RUNNING'].includes(value.decision_status || '')
const findingList = (values: unknown[]) => values.filter((value): value is Finding => typeof value === 'object' && value !== null && 'message' in value)
const prepareText: Record<string, string> = { QUEUED: '等待后台准备', RUNNING: '后台准备中', SUCCEEDED: '准备已完成', FAILED: '准备失败', SUPERSEDED: '已被新任务替代' }
type EvidenceReadout = { items: { ref: SourceReference; source: string; excerpt: string; locator: Record<string, unknown>; native_recommendation: string | null; native_category: string | null }[] }

function CandidateRows({ title, items, selected, compared, onView, onCompare }: { title: string; items: PreparedCandidate[]; selected: string | null; compared: string[]; onView: (id: string) => void; onCompare: (id: string) => void }) {
  if (!items.length) return null
  return <section className="candidate-section"><h3>{title}<span>{items.length}</span></h3>{items.map(item => <article className={`candidate-row ${selected === item.candidate_id ? 'selected' : ''}`} key={item.candidate_id}>
    <button className="candidate-main" onClick={() => onView(item.candidate_id)} aria-pressed={selected === item.candidate_id}><span className="candidate-code">{item.regimen_code}</span><strong>{item.display_name}</strong><span className="candidate-meta">{item.evidence_level !== null ? `Level ${item.evidence_level} · ${item.evidence_grade || ''}` : '证据等级待核验'}{item.evidence_state !== 'VERIFIED' && ' · 依据待核验'}</span></button>
    <div className="candidate-bottom"><span>{item.presentation_region === 'X_EXCLUDED' ? 'X 区 · 仅查看' : `${item.data_labels.length} 项数据提示 · ${item.safety_labels.length} 项风险提示`}</span><button onClick={() => onCompare(item.candidate_id)} aria-pressed={compared.includes(item.candidate_id)}>{compared.includes(item.candidate_id) ? '已加入对比' : '加入对比'}</button></div>
  </article>)}</section>
}

function Findings({ title, items }: { title: string; items: Finding[] }) {
  return <section className="candidate-findings"><h3>{title}<span>{items.length}</span></h3>{items.length ? items.map((item, index) => <p key={`${item.code}:${index}`}>{item.message}</p>) : <p className="quiet-text">本次运行未记录这类提示。</p>}</section>
}

export function ClinicalWorkbench({ contextId, demoMode = false }: { contextId: string | null; demoMode?: boolean }) {
  const [readout, setReadout] = useState<ContextReadout | null>(null)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [actionFailure, setActionFailure] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [clock, setClock] = useState(Date.now())
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<CandidateDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [comparisons, setComparisons] = useState<CandidateDetail[]>([])
  const [comparing, setComparing] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [evidence, setEvidence] = useState<EvidenceReadout | null>(null)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [editor, setEditor] = useState<{ mode: 'view' | 'edit'; detail: CandidateDetail; session?: PatientInstance } | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const generation = useRef(0)
  const commandKey = useRef<{ body: string; key: string } | null>(null)
  const detailPane = useRef<HTMLElement>(null)
  const patientStrip = useRef<HTMLElement>(null)

  useEffect(() => {
    if ((!detail && !comparing) || !window.matchMedia('(max-width: 760px)').matches || !detailPane.current) return
    const topbar = detailPane.current.closest('.shell-content')?.querySelector('.topbar')
    detailPane.current.style.scrollMarginTop = `${(patientStrip.current?.offsetHeight || 0) + (topbar?.getBoundingClientRect().height || 0) + 10}px`
    detailPane.current.scrollIntoView({ block: 'start', behavior: 'auto' })
  }, [detail?.candidate_id, comparing])

  useEffect(() => { generation.current++; setReadout(null); setSelected(null); setDetail(null); setEditor(null); setCompareIds([]); setComparisons([]); setComparing(false); setAgentOpen(false); setEvidenceOpen(false); setEvidence(null); setActionFailure(null); commandKey.current = null; return () => { generation.current++ } }, [contextId])
  useEffect(() => {
    setFailure(null)
    if (!contextId) { setLoading(false); return }
    const controller = new AbortController(); let timer: number | undefined; let disposed = false
    const read = async () => {
      setLoading(true)
      try {
        const result = await getJson<ContextReadout>(`/api/v1/contexts/${contextId}`, controller.signal)
        if (disposed || result.context_id.toLowerCase() !== contextId.toLowerCase()) return
        setReadout(result)
        setFailure(null)
        if (pending(result)) timer = window.setTimeout(read, 1800)
      } catch (error) { if (!disposed && !(error instanceof DOMException && error.name === 'AbortError')) setFailure(message(error)) }
      finally { if (!disposed) setLoading(false) }
    }
    void read(); return () => { disposed = true; controller.abort(); if (timer) window.clearTimeout(timer) }
  }, [contextId, retry])
  useEffect(() => { setSelected(null); setDetail(null); setCompareIds([]); setComparisons([]); setComparing(false); setEvidenceOpen(false); setEditor(null) }, [readout?.generation])
  useEffect(() => {
    setDetail(null); setEvidence(null); setEvidenceOpen(false); setActionFailure(null)
    if (!contextId || !selected) return
    const controller = new AbortController(); setDetailLoading(true)
    getJson<CandidateDetail>(`/api/v1/contexts/${contextId}/candidates/${selected}`, controller.signal).then(value => { if (!controller.signal.aborted) setDetail(value) }).catch(error => { if (!controller.signal.aborted) setActionFailure(message(error)) }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [contextId, selected])
  useEffect(() => {
    if (!evidenceOpen || !selected || !contextId) return
    const controller = new AbortController()
    getJson<EvidenceReadout>(`/api/v1/contexts/${contextId}/candidates/${selected}/evidence`, controller.signal).then(value => { if (!controller.signal.aborted) setEvidence(value) }).catch(error => { if (!controller.signal.aborted) setActionFailure(message(error)) })
    return () => controller.abort()
  }, [contextId, selected, evidenceOpen])
  useEffect(() => {
    if (!readout?.expires_at) return
    const timer = window.setTimeout(() => setClock(Date.now()), Math.max(0, Date.parse(readout.expires_at) - Date.now() + 10))
    return () => window.clearTimeout(timer)
  }, [readout?.expires_at])
  const expired = readout && (readout.context_state !== 'ACTIVE' || Date.parse(readout.expires_at) <= Math.max(clock, Date.now()))
  const ready = Boolean(readout && !expired && readout.prepare_status === 'SUCCEEDED' && readout.decision_status === 'SUCCEEDED')
  const recommended = readout?.candidates.filter(item => item.presentation_region === 'RECOMMENDATION') || []
  const excluded = readout?.candidates.filter(item => item.presentation_region === 'X_EXCLUDED') || []
  const selectedItem = readout?.candidates.find(item => item.candidate_id === selected)
  const fact = (key: string) => { const value = readout?.snapshot?.facts[key]; return value?.status === 'CONFIRMED' ? String(value.value ?? '未提供') : '待核对' }
  const view = (id: string) => { setComparing(false); setSelected(id); if (contextId) void postJson(`/api/v1/contexts/${contextId}/actions`, { kind: 'VIEW', candidate_ids: [id] }).catch(error => setActionFailure(message(error))) }
  const compare = (id: string) => { setCompareIds(previous => previous.includes(id) ? previous.filter(value => value !== id) : previous.length < 3 ? [...previous, id] : previous) }
  const request = async <T,>(path: string, body: unknown): Promise<T> => {
    const encoded = JSON.stringify({ path, body })
    if (commandKey.current?.body !== encoded) commandKey.current = { body: encoded, key: crypto.randomUUID() }
    return postJson<T>(path, body, commandKey.current.key)
  }
  const choose = async () => {
    if (!contextId || !detail || !ready || actionBusy) return
    const fence = generation.current; setActionBusy(true); setActionFailure(null)
    try {
      const result = await request<{ instance_id: string }>(`/api/v1/contexts/${contextId}/candidates/${detail.candidate_id}/select`, {})
      const session = await getJson<PatientInstance>(`/api/v1/contexts/${contextId}/instances/${result.instance_id}`)
      if (fence === generation.current) { setEditor({ mode: 'edit', detail, session }); commandKey.current = null; setRetry(value => value + 1) }
    } catch (error) { if (fence === generation.current) setActionFailure(message(error)) }
    finally { if (fence === generation.current) setActionBusy(false) }
  }
  const resume = async (id: string) => {
    const fence = generation.current; setActionBusy(true); setActionFailure(null)
    try {
      const session = await getJson<PatientInstance>(`/api/v1/contexts/${contextId}/instances/${id}`)
      if (fence === generation.current) setEditor({ mode: session.read_only ? 'view' : 'edit', session, detail: { candidate_id: '', template: session.template, template_hash: '', snapshot: session.snapshot, field_values: session.field_values, assessment: {} } })
    } catch (error) { if (fence === generation.current) setActionFailure(message(error)) }
    finally { if (fence === generation.current) setActionBusy(false) }
  }
  const openCompare = async () => {
    if (!contextId || compareIds.length < 2) return
    const fence = generation.current; setActionBusy(true); setActionFailure(null)
    try {
      const result = await Promise.all(compareIds.map(id => getJson<CandidateDetail>(`/api/v1/contexts/${contextId}/candidates/${id}`)))
      await request(`/api/v1/contexts/${contextId}/actions`, { kind: 'COMPARE', candidate_ids: compareIds })
      if (fence === generation.current) { setComparisons(result); setComparing(true); commandKey.current = null }
    } catch (error) { if (fence === generation.current) setActionFailure(message(error)) }
    finally { if (fence === generation.current) setActionBusy(false) }
  }
  const refresh = async () => {
    if (!contextId || !readout || actionBusy) return
    const fence = generation.current; setActionBusy(true); setActionFailure(null)
    try { await request(`/api/v1/contexts/${contextId}/refresh`, { expected_generation: readout.generation }); if (fence === generation.current) { setRetry(value => value + 1); commandKey.current = null } }
    catch (error) { if (fence === generation.current) setActionFailure(message(error)) }
    finally { if (fence === generation.current) setActionBusy(false) }
  }

  return <main className="clinical-workbench">
    <header className="workbench-heading"><div><span className="eyebrow">PATIENT WORKSPACE</span><h1>本次诊疗</h1></div><div className="workbench-toolbar"><button onClick={refresh} disabled={!readout || Boolean(expired) || actionBusy || loading}>更新患者数据</button><button className="primary-button" onClick={() => setAgentOpen(true)} disabled={!ready}>✦ 智能体解读</button></div></header>
    <section ref={patientStrip} className="patient-context-strip" aria-label="当前患者及准备状态"><div><span>当前患者</span><strong>{readout ? fact('patient_name') === '待核对' ? readout.patient_ref : fact('patient_name') : '等待工作站传入'}</strong><small>{readout?.patient_ref || '尚无可信患者上下文'}</small></div><div><span>本次就诊</span><strong>{readout?.encounter_ref || '—'}</strong></div><div><span>疾病 / 病理</span><strong>{readout ? fact('disease') : '—'}</strong><small>{readout ? fact('pathology') : ''}</small></div><div><span>后台准备</span><strong>{expired ? '本次上下文已失效' : prepareText[readout?.prepare_status || ''] || '等待准备'}</strong><small>{readout?.snapshot?.captured_at ? `数据时间 ${new Date(readout.snapshot.captured_at).toLocaleTimeString()}` : '尚未生成患者快照'}</small></div></section>
    {readout && <div className="workbench-mode-note">{demoMode ? '方案依据待核验 · 请核对患者资料与给药安排' : '内部测试流程 · 依据待核验 · 尚未联通院方交付服务'}{readout.notices?.length ? <details><summary>{readout.notices.length} 项准备提示</summary>{readout.notices.map((item, i) => <p key={i}>{item.message}</p>)}</details> : null}</div>}
    {actionFailure && <div className="inline-error" role="alert">{actionFailure}<button onClick={() => setActionFailure(null)} aria-label="关闭错误提示">×</button></div>}
    {!contextId ? <section className="clinical-empty-state"><span className="empty-symbol">✦</span><h2>等待当前患者</h2><p>院内工作站进入或切换患者时，挂件会在后台读取数据并准备候选。医生打开右下角入口，即可查看本次准备结果。</p><p className="quiet-text">当前未收到可信工作站上下文；公共方案可以从「浏览」入口查看。</p></section> : failure ? <section className="clinical-empty-state" role="alert"><h2>无法读取当前患者</h2><p>{failure}</p><button onClick={() => setRetry(value => value + 1)}>重新读取</button></section> : !readout ? <section className="clinical-empty-state" role="status"><h2>正在读取后台准备状态</h2><p>请稍候。</p></section> : expired ? <section className="clinical-empty-state"><h2>这次上下文已经失效</h2><p>请从院内工作站重新打开当前患者。此处的旧结果不能用于另一位患者。</p></section> : !ready ? <section className="clinical-empty-state" role="status"><h2>{readout.prepare_status === 'FAILED' ? '后台准备未完成' : pending(readout) ? '后台正在准备候选' : '等待本次运行结果'}</h2><p>{readout.prepare_status === 'FAILED' ? `读取或配置未通过（${readout.prepare_error_code || '原因待查询'}）。这不表示患者没有适用方案。` : `当前步骤：${prepareText[readout.prepare_status || ''] || '等待运行'}。已准备的结果会在这里更新。`}</p></section> : <div className="clinical-split">
      <aside className="clinical-master"><div className="candidate-list-heading"><h2>方案候选<span>{readout.candidates.length}</span></h2><p>先查看，再对比或选用</p></div><div className="candidate-list-scroll">{!readout.candidates.length && <p className="list-empty">本次没有可展示的候选，请核对患者数据与适用关系配置。</p>}{Boolean(readout.instances?.length) && <section className="patient-instances"><h3>本次就诊已选方案</h3>{readout.instances?.map((item, index) => <button key={item.id} onClick={() => resume(item.id)} disabled={actionBusy}><span>{item.display_name || `患者方案 ${readout.instances!.length - index}`}</span><small>{item.current_confirmed_revision_id === item.current_revision_id && item.current_revision_id ? '已确认' : item.current_revision_id ? '已保存' : '尚未保存'}</small><span>{item.read_only ? '查看既有方案 →' : '继续编辑 →'}</span></button>)}</section>}<CandidateRows title="推荐区" items={recommended} selected={selected} compared={compareIds} onView={view} onCompare={compare} /><CandidateRows title="X 区 · 查看理由" items={excluded} selected={selected} compared={compareIds} onView={view} onCompare={compare} /></div><div className="compare-tray"><span>已选 {compareIds.length} / 3 份</span><button onClick={openCompare} disabled={compareIds.length < 2 || actionBusy}>对比方案</button></div></aside>
      <section ref={detailPane} className="clinical-detail">{comparing ? <><header className="candidate-detail-heading"><div><span className="eyebrow">COMPARE</span><h2>方案对比</h2></div><button onClick={() => setComparing(false)}>返回详情</button></header><div className="comparison-grid">{comparisons.map(item => { const result = readout.candidates.find(c => c.candidate_id === item.candidate_id); return <article key={item.candidate_id}><span className="candidate-code">{item.template.regimen_code}</span><h3>{item.template.display_name}</h3><p>{result?.evidence_level != null ? `Level ${result.evidence_level} · ${result.evidence_grade}` : '等级待核验'}</p>{item.template.medications.map(med => <div className="comparison-drug" key={med.item_key}><strong>{med.generic_name || med.source_drug_name}</strong><span>{med.standard_dose_text || '原文剂量待核对'}</span><small>{med.route_text} · {med.administration_day_text}</small></div>)}<p>{result?.data_labels.length} 项数据提示 · {result?.safety_labels.length} 项风险提示</p><button onClick={() => view(item.candidate_id)}>查看这份方案</button></article> })}</div></> : detailLoading ? <div className="clinical-detail-empty" role="status"><h2>正在读取固定版本</h2></div> : detail ? <>
        <header className="candidate-detail-heading"><div><span className="eyebrow">{detail.template.regimen_code} / V{detail.template.version_no}</span><h2>{detail.template.display_name}</h2><p>{selectedItem?.evidence_level != null ? `Level ${selectedItem.evidence_level} · ${selectedItem.evidence_grade}` : '证据等级待核验'} · {selectedItem?.presentation_region === 'X_EXCLUDED' ? 'X 区方案' : '推荐区候选'}</p></div></header>
        <div className="candidate-actions"><button onClick={() => setEditor({ mode: 'view', detail })}>查看完整表单</button><button onClick={() => compare(detail.candidate_id)}>{compareIds.includes(detail.candidate_id) ? '移出对比' : '加入对比'}</button><button className="primary-button" disabled={actionBusy || selectedItem?.presentation_region === 'X_EXCLUDED'} onClick={choose}>{actionBusy ? '处理中…' : '选用并编辑本次方案'}</button></div>
        {selectedItem?.x_reason_code && <div className="x-reason"><strong>X 理由</strong><p>{selectedItem.x_reason_code === 'ABSOLUTE_CONTRAINDICATION' ? '配置规则命中明确绝对禁忌' : '来源关系属于适应症外相关方案'}</p>{Array.isArray(detail.assessment.x_basis) && detail.assessment.x_basis.map((basis, i) => <details key={i}><summary>查看来源依据 {i + 1}</summary><p>{typeof basis === 'object' && basis && 'message' in basis ? String(basis.message) : '请核对关联规则及原始来源'}</p></details>)}</div>}
        <section className="candidate-composition"><h3>方案组成<span>{detail.template.medications.length} 项</span></h3>{detail.template.medications.map((item, index) => <div key={item.item_key}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{item.generic_name || item.source_drug_name}</strong><p>{item.standard_dose_text || '原文剂量待核对'} · {item.route_text || '途径待核对'} · {item.administration_day_text || '给药安排待核对'}</p></div></div>)}</section>
        <div className="candidate-label-grid"><Findings title="数据提示" items={findingList(selectedItem?.data_labels || [])} /><Findings title="安全提示" items={findingList(selectedItem?.safety_labels || [])} /></div>
        <section className="candidate-evidence"><h3>本次关联证据</h3><p>按当前候选保存的版本读取；证据标签与原始推荐等级分别保留。</p><button onClick={() => setEvidenceOpen(value => !value)}>{evidenceOpen ? '收起原文' : '展开证据原文与来源'}</button>{evidenceOpen && (evidence ? evidence.items.length ? evidence.items.map(item => <article key={item.ref.id}><strong>{item.source}</strong><small>证据版本 {item.ref.version} · {item.native_recommendation || '原始推荐等级未提供'} · {item.native_category || '原始类别未提供'}</small><blockquote>{item.excerpt}</blockquote><details><summary>查看来源位置</summary>{Object.entries(item.locator).map(([key, value]) => <p key={key}>{key}：{typeof value === 'string' || typeof value === 'number' ? String(value) : '详见原始来源'}</p>)}</details></article>) : <p>当前候选没有保存可读取的关联证据。</p> : <p role="status">正在读取固定证据…</p>)}</section>
      </> : <div className="clinical-detail-empty"><span>↗</span><h2>先选择一份候选</h2><p>这里显示方案组成、数据与安全提示；完整表单用于查看和本次编辑。</p></div>}</section>
    </div>}
    {agentOpen && contextId && <div className="agent-drawer-backdrop" onClick={() => setAgentOpen(false)}><aside className="agent-drawer" role="dialog" aria-modal="true" aria-label="智能体解读" onClick={event => event.stopPropagation()}><button className="drawer-close" onClick={() => setAgentOpen(false)}>关闭解读 ×</button><AgentPanel contextId={contextId} kind="RECOMMENDATION" /></aside></div>}
    {editor && contextId && <PatientEditor key={editor.session?.instance_id || editor.detail.candidate_id} detail={editor.detail.template} mode={editor.mode} session={editor.session} preview={editor.detail} contextId={contextId} onClose={() => { setEditor(null); setRetry(value => value + 1) }} onSaved={() => setRetry(value => value + 1)} />}
  </main>
}
