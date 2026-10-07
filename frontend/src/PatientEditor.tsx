import { useEffect, useRef, useState } from 'react'
import { ApiFailure, getJson, postJson, type PatientInstance, type RegimenDetail, type Finding, type CandidateDetail } from './api'
import { BlueprintDocument, type MedicationDraft } from './BlueprintDocument'
import { AgentPanel } from './AgentPanel'
import { DemoDeliveryPanel } from './DemoDeliveryPanel'
import { rebaseValues } from './editor-conflict'
import './patient-editor.css'

type Section = 'document' | 'sources' | 'history' | 'review' | 'delivery'
const sections: { id: Section; label: string }[] = [{ id: 'document', label: '完整方案表单' }, { id: 'sources', label: '患者数据与依据' }, { id: 'history', label: '修订记录' }, { id: 'review', label: '独立复核' }, { id: 'delivery', label: '院方交付检查' }]
interface Readiness { revision_hash: string; checks: { code: string; message: string; state: string }[]; lines: { line_no: number; name: string; missing: string[] }[] }
const emptyMedication = (): MedicationDraft => ({ dose: '', route: '', frequency: '', day: '', note: '' })
function medicationFields(session?: PatientInstance): Record<string, MedicationDraft> {
  return Object.fromEntries(Object.entries(session?.medication_values || {}).map(([key, value]) => [key, { ...emptyMedication(), dose: value.actual_dose_text, day: value.administration_day_text, note: value.instructions }]))
}
function flatValues(fields: Record<string, unknown>, meds: Record<string, MedicationDraft>): Record<string, string> {
  return { ...Object.fromEntries(Object.entries(fields).map(([key, value]) => [JSON.stringify(['field', key]), value == null ? '' : String(value)])), ...Object.fromEntries(Object.entries(meds).flatMap(([item, values]) => (['dose', 'day', 'note'] as const).map(key => [JSON.stringify(['medication', item, key]), values[key]]))) }
}

function CalculationResult({ label, value }: { label: string; value: unknown }) {
  const result = value && typeof value === 'object' ? value as { state?: string; value?: number; unit?: string; formula_version?: string; display_value?: string; reasons?: Finding[]; inputs?: Record<string, unknown> } : {}
  return <article className="calculation-result"><strong>{label}</strong><p>{result.state === 'COMPUTED' ? `标准参考剂量 ${result.display_value || result.value} ${result.unit || ''}` : '当前不可计算'}</p>{result.reasons?.map((reason, i) => <small key={i}>{reason.message}</small>)}{result.formula_version && <details><summary>计算公式与输入</summary><p>{result.formula_version}</p>{Object.entries(result.inputs || {}).map(([key, item]) => <p key={key}>{key}：{typeof item === 'object' ? '固定快照来源' : String(item)}</p>)}</details>}</article>
}

export function PatientEditor({ detail, mode, onClose, session, contextId, onSaved, preview, returnLabel }: { detail: RegimenDetail; mode: 'view' | 'edit'; onClose: () => void; session?: PatientInstance; contextId?: string; onSaved?: () => void; preview?: CandidateDetail; returnLabel?: string }) {
  const [section, setSection] = useState<Section>('document')
  const [current, setCurrent] = useState(session)
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(Object.fromEntries(Object.entries(session?.field_values || preview?.field_values || {}).map(([key, value]) => [key, value == null ? '' : String(value)])))
  const [medicationValues, setMedicationValues] = useState<Record<string, MedicationDraft>>(medicationFields(session))
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [leavePrompt, setLeavePrompt] = useState(false)
  const [confirmPrompt, setConfirmPrompt] = useState(false)
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [changeReason, setChangeReason] = useState('')
  const [reviewerRunId, setReviewerRunId] = useState<string | null>(null)
  const [historical, setHistorical] = useState<PatientInstance | null>(null)
  const [conflict, setConflict] = useState<PatientInstance | null>(null)
  const [conflictChoices, setConflictChoices] = useState<Record<string, 'local' | 'latest'>>({})
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [readinessFailure, setReadinessFailure] = useState<string | null>(null)
  const [demoMode, setDemoMode] = useState(false)
  const alive = useRef(true)
  const operation = useRef<{ body: string; key: string } | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const editable = mode === 'edit' && Boolean(current && contextId && !current.read_only)
  const issues: Finding[] = current?.issues || []
  const close = () => dirty ? setLeavePrompt(true) : onClose()
  useEffect(() => { alive.current = true; root.current?.querySelector<HTMLButtonElement>('button')?.focus(); return () => { alive.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController()
    void getJson<{ demo_mode?: boolean }>('/api/v1/status', controller.signal).then(value => { if (!controller.signal.aborted) setDemoMode(Boolean(value.demo_mode)) }).catch(() => {})
    return () => controller.abort()
  }, [])
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (historical) return
      if (event.key === 'Escape' && !busy) { event.preventDefault(); if (conflict) setConflict(null); else if (confirmPrompt) setConfirmPrompt(false); else if (leavePrompt) setLeavePrompt(false); else close() }
      if (event.key !== 'Tab') return
      const container = root.current?.querySelector<HTMLElement>('.editor-dialog-backdrop') || root.current
      const focusable = container?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]')
      if (!focusable?.length) return
      const first = focusable[0], last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler)
  }, [dirty, busy, confirmPrompt, leavePrompt, historical, conflict])
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
  useEffect(() => {
    setReadiness(null); setReadinessFailure(null)
    if (demoMode || section !== 'delivery' || !current?.revision_id || !contextId) return
    const controller = new AbortController()
    void getJson<Readiness>(`/api/v1/contexts/${contextId}/instances/${current.instance_id}/hospital-readiness`, controller.signal).then(value => { if (!controller.signal.aborted) setReadiness(value) }).catch(error => { if (!controller.signal.aborted) setReadinessFailure(error instanceof ApiFailure ? error.message : '交付条件读取失败') })
    return () => controller.abort()
  }, [section, current?.revision_id, current?.confirmed_revision_id, contextId, demoMode])
  const fillExample = async () => {
    if (!current || !contextId || !editable || busy) return
    setBusy(true); setFailure(null)
    try {
      const values = await getJson<{ field_values: Record<string, unknown>; medication_values: PatientInstance['medication_values'] }>(`/workstation/contexts/${contextId}/instances/${current.instance_id}/example`)
      if (!alive.current) return
      setFieldValues(previous => ({ ...previous, ...Object.fromEntries(Object.entries(values.field_values).map(([key, value]) => [key, String(value)])) }))
      setMedicationValues(previous => ({ ...previous, ...medicationFields({ ...current, medication_values: values.medication_values }) }))
      setDirty(true); setNotice('已填入本次预设值，请核对治疗周期、日期及各药实际剂量后保存。')
    } catch (error) { if (alive.current) setFailure(error instanceof ApiFailure ? error.message : '预设值读取失败') }
    finally { if (alive.current) setBusy(false) }
  }
  const reload = async () => {
    const next = await getJson<PatientInstance>(`/api/v1/contexts/${contextId}/instances/${current?.instance_id}`)
    if (!alive.current) return
    setCurrent(next); setFieldValues(Object.fromEntries(Object.entries(next.field_values).map(([key, value]) => [key, value == null ? '' : String(value)])))
    setMedicationValues(medicationFields(next)); setDirty(false); setChangeReason(''); operation.current = null; onSaved?.()
  }
  const save = async () => {
    if (!current || !contextId || !editable || busy) return
    const fields = Object.fromEntries(detail.fields.filter(field => field.edit_policy === 'RUNTIME_EDITABLE' && field.widget_type !== 'doctor-selector').map(field => [field.field_key, fieldValues[field.field_key] || null]))
    const meds = Object.fromEntries(detail.medications.map(item => { const value = medicationValues[item.item_key] || emptyMedication(); return [item.item_key, { actual_dose_text: value.dose, administration_day_text: value.day, instructions: value.note }] }))
    const payload = { expected_row_version: current.row_version, base_revision_id: current.revision_id, field_values: fields, medication_values: meds, change_reason: changeReason || null }
    const body = JSON.stringify(payload)
    if (operation.current?.body !== body) operation.current = { body, key: crypto.randomUUID() }
    setBusy(true); setFailure(null); setNotice(null)
    try { await postJson(`/api/v1/contexts/${contextId}/instances/${current.instance_id}/revisions`, payload, operation.current.key); await reload(); if (alive.current) setNotice('已保存本次修订。确认是下一步独立操作。') }
    catch (error) {
      if (!alive.current) return
      setFailure(error instanceof ApiFailure ? error.message : '保存未完成')
      if (error instanceof ApiFailure && error.code === 'REVISION_CONFLICT') {
        try { const latest = await getJson<PatientInstance>(`/api/v1/contexts/${contextId}/instances/${current.instance_id}`); if (alive.current) { setConflict(latest); setConflictChoices({}) } }
        catch (readError) { if (alive.current) setFailure(readError instanceof ApiFailure ? readError.message : '最新修订读取失败，本地修改已保留') }
      }
    }
    finally { if (alive.current) setBusy(false) }
  }
  const confirm = async () => {
    if (!current?.revision_id || !current.revision_hash || busy || dirty) return
    setBusy(true); setFailure(null)
    const body = { revision_id: current.revision_id, revision_hash: current.revision_hash, expected_row_version: current.row_version, acknowledged_codes: acknowledged }
    const encoded = JSON.stringify(body)
    if (operation.current?.body !== encoded) operation.current = { body: encoded, key: crypto.randomUUID() }
    try { const result = await postJson<{ reviewer_status: string; reviewer_run_id?: string; reviewer_error_code?: string }>(`/api/v1/contexts/${contextId}/instances/${current.instance_id}/confirm`, body, operation.current.key); await reload(); if (alive.current) { setReviewerRunId(result.reviewer_run_id || null); setConfirmPrompt(false); setNotice(`本次修订已确认；尚未提交院方。${result.reviewer_status === 'QUEUED' ? '独立复核已排队。' : result.reviewer_status === 'FAILED' ? '模型尚未接入，复核未执行。' : '复核请求未完成，请查看复核记录。'}`) } }
    catch (error) { if (alive.current) setFailure(error instanceof ApiFailure ? error.message : '确认未完成') }
    finally { if (alive.current) setBusy(false) }
  }
  const updateField = (key: string, value: string) => { setFieldValues(previous => ({ ...previous, [key]: value })); setDirty(true); setNotice(null) }
  const updateMedication = (itemKey: string, key: keyof MedicationDraft, value: string) => { setMedicationValues(previous => ({ ...previous, [itemKey]: { ...emptyMedication(), ...previous[itemKey], [key]: value } })); setDirty(true); setNotice(null) }
  const rebase = conflict && current ? rebaseValues(flatValues(current.field_values, medicationFields(current)), flatValues(fieldValues, medicationValues), flatValues(conflict.field_values, medicationFields(conflict)), conflictChoices) : null
  const applyRebase = (keepLocal: boolean) => {
    if (!conflict || (keepLocal && rebase?.unresolved.length)) return
    const fields: Record<string, string> = {}, meds = medicationFields(conflict)
    const next = keepLocal && rebase ? rebase.values : flatValues(conflict.field_values, meds)
    for (const [encoded, value] of Object.entries(next)) { const [kind, key, subkey] = JSON.parse(encoded) as string[]; if (kind === 'field') fields[key] = value; else meds[key] = { ...emptyMedication(), ...meds[key], [subkey]: value } }
    setCurrent(conflict); setFieldValues(fields); setMedicationValues(meds); setDirty(keepLocal); setConflict(null); setFailure(null); operation.current = null; setNotice(keepLocal ? '已以最新修订为基础保留选择的修改。请核对后重新保存，尚未写入数据库。' : '已重新加载最新修订。'); if (!keepLocal) setChangeReason('')
  }
  const viewHistory = async (revisionId: string) => {
    if (!current || !contextId || busy) return
    setBusy(true); setFailure(null)
    try { const result = await getJson<PatientInstance>(`/api/v1/contexts/${contextId}/instances/${current.instance_id}/revisions/${revisionId}`); if (alive.current) setHistorical(result) }
    catch (error) { if (alive.current) setFailure(error instanceof ApiFailure ? error.message : '历史修订读取失败') }
    finally { if (alive.current) setBusy(false) }
  }
  const conflictLabel = (encoded: string) => { const [kind, key, subkey] = JSON.parse(encoded) as string[]; return kind === 'field' ? detail.fields.find(field => field.field_key === key)?.label || key : `${detail.medications.find(item => item.item_key === key)?.source_drug_name || key} · ${{ dose: '实际剂量', day: '给药日期', note: '说明' }[subkey as 'dose' | 'day' | 'note']}` }
  const snapshot = current?.snapshot || preview?.snapshot
  const patientName = snapshot?.facts.patient_name
  return <div ref={root} className={`patient-editor ${mode === 'view' ? 'editor-view' : ''}`} role="dialog" aria-modal="true" aria-label={editable ? '患者方案编辑' : '完整方案表单查看'}>
    <header className="editor-topbar"><div><span className="editor-kicker">{current || preview ? '患者方案' : '方案库'} / 完整表单</span><h2>{editable ? '编辑本次方案' : current ? '查看已保存修订' : '查看方案表单'} <span>{mode === 'view' && current ? '历史内容 · 只读查看' : dirty ? '有未保存修改' : current?.confirmed_revision_id === current?.revision_id && current?.revision_id ? '本次修订已确认' : current?.revision_id ? '已保存，可单独确认' : '固定来源版本'}</span></h2></div><button className="editor-close" onClick={close}>{returnLabel || `返回${contextId ? '患者候选' : '方案库'}`}</button></header>
    <div className="editor-template"><div><span>来源方案</span><strong>{detail.regimen_code} · {detail.display_name}</strong></div><div><span>固定版本</span><strong>V{detail.version_no}</strong></div><div><span>当前患者 / 就诊</span><strong>{snapshot ? `${patientName?.status === 'CONFIRMED' ? patientName.value : snapshot.patient_ref} / ${snapshot.encounter_ref}` : '公共方案表单'}</strong></div></div>
    <div className="editor-body">
      <nav className="editor-sections" aria-label="方案内容"><p>内容导航</p>{sections.filter(item => current || item.id === 'document').map(item => <button key={item.id} className={section === item.id ? 'selected' : ''} onClick={() => setSection(item.id)}>{item.label}</button>)}</nav>
      <main className="editor-main">
        {failure && <div className="inline-error" role="alert">{failure}</div>}{notice && <div className="inline-success" role="status">{notice}</div>}
        {section === 'document' && <fieldset className="editor-form-fields" disabled={busy}><BlueprintDocument detail={detail} mode={editable ? 'edit' : 'view'} values={fieldValues} medicationValues={medicationValues} onFieldChange={updateField} onMedicationChange={updateMedication} /></fieldset>}
        {section === 'sources' && current && <section className="snapshot-facts"><h3>本次患者快照</h3><p>采集时间：{new Date(current.snapshot.captured_at).toLocaleString()}</p>{Object.entries(current.snapshot.facts).map(([key, fact]) => <div key={key}><strong>{({ patient_name: '患者姓名', disease: '疾病', disease_code: '疾病', pathology: '病理', pathology_code: '病理', height_cm: '身高', weight_kg: '体重', gfr_ml_min: '实测肾小球滤过率' } as Record<string, string>)[key] || key}</strong><span>{String(fact.value ?? '缺失')} {fact.unit || ''}</span><small>{fact.status === 'CONFIRMED' ? '来源已确认' : '需核对'} · {fact.source.namespace} / {fact.source.version}</small></div>)}<h3>固定依据</h3>{[...current.data_labels, ...current.safety_labels].map((finding, index) => <p key={index}>{finding.message}</p>)}<h3>程序计算</h3>{Object.entries(current.calculations).length ? Object.entries(current.calculations).map(([key, result]) => <CalculationResult key={key} label={detail.medications.find(item => item.item_key === key)?.source_drug_name || key} value={result} />) : <p>公式及测量有效期尚未确认，没有生成计算结果。</p>}</section>}
        {section === 'history' && current && <section className="revision-history"><h3>不可变修订记录</h3>{current.history.length ? current.history.map(item => <article key={item.id}><strong>第 {item.revision_no} 次修订</strong><time>{new Date(item.created_at).toLocaleString()}</time><p>{item.change_reason || '首次保存'}</p><small>{item.content_hash.slice(0, 16)}…</small><button disabled={busy} onClick={() => viewHistory(item.id)}>查看这次保存的完整表单</button></article>) : <p>尚未保存。本页编辑不会修改公共方案库。</p>}</section>}
        {section === 'review' && current && contextId && <AgentPanel contextId={contextId} kind="REVIEWER" revisionId={current.revision_id} initialRunId={reviewerRunId} />}
        {section === 'delivery' && demoMode && current && contextId && <DemoDeliveryPanel contextId={contextId} instanceId={current.instance_id} revisionId={current.revision_id} confirmed={Boolean(current.revision_id && current.revision_id === current.confirmed_revision_id)} editable={editable && !dirty} />}
        {section === 'delivery' && !demoMode && <section className="snapshot-facts"><h3>本地交付条件检查</h3><p>这里只检查已保存修订的准备情况，没有向院方发送预校验或医嘱请求。</p>{!current?.revision_id ? <p>先保存本次修订，再检查交付条件。</p> : readinessFailure ? <p role="alert">{readinessFailure}</p> : !readiness ? <p role="status">正在读取这次修订的条件…</p> : <>{readiness.checks.map(item => <p key={item.code}><strong>{item.state === 'SATISFIED' ? '已满足' : '待完成'}</strong> · {item.message}</p>)}<h3>逐条医嘱待配置项</h3>{readiness.lines.map(item => <article className="calculation-result" key={item.line_no}><strong>{item.line_no}. {item.name}</strong><p>{item.missing.map(key => ({ hospital_item_code: '院内项目编码', dose_value: '结构化实际剂量', dose_unit: '剂量单位', quantity: '开立数量', quantity_unit: '数量单位', route_code: '途径编码', frequency_code: '频次编码', start_day: '治疗起始日', long_term_flag: '长期或临时标记' } as Record<string, string>)[key] || '院方字段').join('、') || '仍需核对映射和提交权限'}</p></article>)}</>}</section>}
      </main>
      <aside className="editor-issues"><span className="editor-kicker">核对事项</span><h3>{current ? '保存与确认分开' : '公共方案查看'}</h3>{!current ? <p>这里呈现固定方案的完整表单。患者工作台选用后，在同一张表单中编辑本次方案。</p> : <><p>原文剂量作为参考。实际剂量由医生核对填写，医院编码不会自动补造。</p>{issues.map((issue, index) => <p key={issue.code}><span>{String(index + 1).padStart(2, '0')}</span>{issue.message}</p>)}{!issues.length && <p>保存后将列出本次方案的待核对事项。</p>}{editable && current.revision_id && <label className="change-reason">本次修改原因<textarea value={changeReason} onChange={event => setChangeReason(event.target.value)} placeholder="再次保存前填写修改原因" /></label>}</>}</aside>
    </div>
    <footer className="editor-footer"><div><strong>{current ? '本次患者方案' : '公共固定表单'}</strong><span>{current ? demoMode ? '请核对患者资料、治疗日期与实际剂量' : '内部测试流程 · 尚未联通院方服务' : '查看不产生患者方案'}</span></div>{editable && <div className="editor-footer-actions">{demoMode && <button onClick={fillExample} disabled={busy}>填入预设值</button>}<button onClick={() => setSection('review')} disabled={busy || !current?.revision_id}>独立复核</button><button onClick={save} disabled={busy || (!dirty && Boolean(current?.revision_id)) || (Boolean(current?.revision_id) && !changeReason.trim())}>{busy ? '处理中…' : '保存本次修订'}</button><button className="editor-primary-action" onClick={() => { setAcknowledged([]); setConfirmPrompt(true) }} disabled={busy || dirty || !current?.revision_id || current.confirmed_revision_id === current.revision_id}>确认本次方案</button></div>}</footer>
    {leavePrompt && <div className="editor-dialog-backdrop"><section className="editor-leave-dialog" role="alertdialog" aria-modal="true" aria-labelledby="leave-title"><h3 id="leave-title">本次修改尚未保存</h3><p>返回会离开本页，已保存修订保持可读取。</p><div><button onClick={() => setLeavePrompt(false)}>继续编辑</button><button className="danger" onClick={onClose}>放弃本次修改并返回</button></div></section></div>}
    {confirmPrompt && <div className="editor-dialog-backdrop"><section className="editor-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title"><h3 id="confirm-title">确认已保存的这次修订</h3><p>请逐项核对。确认只记录当前修订，医嘱提交需在“院方交付检查”中单独执行。</p>{issues.map(issue => <label key={issue.code}><input type="checkbox" checked={acknowledged.includes(issue.code)} onChange={event => setAcknowledged(previous => event.target.checked ? [...previous, issue.code] : previous.filter(code => code !== issue.code))} />{issue.message}</label>)}{failure && <p role="alert">{failure}</p>}<div><button onClick={() => setConfirmPrompt(false)} disabled={busy}>继续核对</button><button onClick={confirm} disabled={busy || !issues.every(issue => acknowledged.includes(issue.code))}>确认这次修订</button></div></section></div>}
    {conflict && <div className="editor-dialog-backdrop"><section className="editor-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="conflict-title"><h3 id="conflict-title">已有另一份更新的修订</h3><p>本地修改已保留。不同字段的修改可合并；同一字段被同时修改时，请逐项选择。选择后仍需重新保存。</p>{rebase?.collisions.map(item => <div className="edit-collision" key={item.key}><strong>{conflictLabel(item.key)}</strong><small>编辑前：{item.before || '空'}</small><label><input type="radio" name={item.key} checked={conflictChoices[item.key] === 'latest'} onChange={() => setConflictChoices(values => ({ ...values, [item.key]: 'latest' }))} />采用最新值：{item.latest || '空'}</label><label><input type="radio" name={item.key} checked={conflictChoices[item.key] === 'local'} onChange={() => setConflictChoices(values => ({ ...values, [item.key]: 'local' }))} />保留本地值：{item.local || '空'}</label></div>)}<div><button onClick={() => setConflict(null)}>返回保留本地修改</button><button onClick={() => applyRebase(false)}>加载最新修订</button><button disabled={Boolean(rebase?.unresolved.length)} onClick={() => applyRebase(true)}>核对并合并本地修改</button></div></section></div>}
    {historical && <PatientEditor key={historical.revision_id} detail={historical.template} mode="view" session={historical} contextId={contextId} onClose={() => setHistorical(null)} returnLabel="返回修订记录" />}
  </div>
}
