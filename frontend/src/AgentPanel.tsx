import { useEffect, useRef, useState } from 'react'
import { ApiFailure, getJson, postJson, type AgentReadout, type SourceReference } from './api'

const toolLabels: Record<string, string> = { read_snapshot: '患者数据', read_candidates: '当前候选', read_plan: '固定方案版本', read_evidence: '关联证据', read_rules: '规则与依据', read_calculations: '程序计算', read_revision: '已保存修订', search_knowledge: '知识检索' }
function Sources({ refs }: { refs: SourceReference[] }) {
  return <details className="agent-sources"><summary>查看引用来源 · {refs.length}</summary>{refs.map(ref => <div key={`${ref.namespace}:${ref.id}:${ref.version}`}><strong>{({ 'clinical.clinical_snapshot': '患者快照', 'knowledge.evidence_record_version': '证据版本', 'clinical.patient_regimen_revision': '患者方案修订', 'catalog_bridge.template_version_reference': '固定方案表单' } as Record<string, string>)[ref.namespace] || '固定来源'}</strong><small>{ref.id} · V{ref.version} · {ref.content_hash.slice(0, 12)}</small></div>)}</details>
}

export function AgentPanel({ contextId, kind, revisionId, initialRunId }: { contextId: string; kind: 'RECOMMENDATION' | 'REVIEWER'; revisionId?: string | null; initialRunId?: string | null }) {
  const [run, setRun] = useState<AgentReadout | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [retry, setRetry] = useState(0)
  const generation = useRef(0)
  const requestKey = useRef<{ body: string; key: string } | null>(null)
  useEffect(() => {
    generation.current++; requestKey.current = null; setRun(null); setRunId(initialRunId || null); setFailure(null); setBusy(false)
    const controller = new AbortController()
    if (!initialRunId && (kind !== 'REVIEWER' || revisionId)) {
      const params = new URLSearchParams({ kind }); if (revisionId) params.set('revision_id', revisionId)
      void getJson<{ items: { id: string }[] }>(`/api/v1/contexts/${contextId}/agent-runs?${params}`, controller.signal).then(value => { if (!controller.signal.aborted && value.items[0]) setRunId(value.items[0].id) }).catch(error => { if (!controller.signal.aborted) setFailure(error instanceof ApiFailure ? error.message : '复核记录读取失败') })
    }
    return () => { generation.current++; controller.abort() }
  }, [contextId, kind, revisionId, initialRunId])
  useEffect(() => {
    if (!runId) return
    const controller = new AbortController(); let timer: number | undefined
    const poll = async () => {
      try {
        const value = await getJson<AgentReadout>(`/api/v1/contexts/${contextId}/agent-runs/${runId}`, controller.signal)
        if (controller.signal.aborted) return
        setRun(value); setBusy(value.status === 'QUEUED' || value.status === 'RUNNING')
        if (value.status === 'QUEUED' || value.status === 'RUNNING') timer = window.setTimeout(poll, 1200)
      } catch (error) { if (!controller.signal.aborted) { setBusy(false); setFailure(error instanceof ApiFailure ? error.message : '运行状态读取失败') } }
    }
    void poll(); return () => { controller.abort(); if (timer) window.clearTimeout(timer) }
  }, [contextId, runId, retry])
  const start = async () => {
    const current = generation.current
    const payload = { kind, revision_id: kind === 'REVIEWER' ? revisionId : null, question }
    const body = JSON.stringify(payload)
    if (requestKey.current?.body !== body) requestKey.current = { body, key: crypto.randomUUID() }
    setBusy(true); setFailure(null)
    try {
      const response = await postJson<{ agent_run_id: string }>(`/api/v1/contexts/${contextId}/agent-runs`, payload, requestKey.current.key)
      if (current === generation.current) { requestKey.current = null; setRun(null); setRunId(response.agent_run_id); setRetry(value => value + 1) }
    } catch (error) { if (current === generation.current) { setBusy(false); setFailure(error instanceof ApiFailure ? error.message : '请求未完成') } }
  }
  const output = run?.outputs.find(item => item.validation_state === 'VALID')?.structured_payload
  return <section className="agent-panel">
    <div className="agent-heading"><span className="agent-icon">✦</span><div><h3>{kind === 'REVIEWER' ? '独立方案复核' : '智能体解读'}</h3><p>{kind === 'REVIEWER' ? '检查已保存的确切修订与引用，发现问题，不改写医生方案。' : '结合本次患者数据、候选、规则和证据，生成可追溯的说明。'}</p></div></div>
    {kind === 'RECOMMENDATION' && <label className="agent-question">需要补充核对的问题<textarea value={question} onChange={event => { setQuestion(event.target.value); requestKey.current = null }} placeholder="例如：解释候选之间的证据差异" maxLength={2000} /></label>}
    <button className="primary-button" onClick={start} disabled={busy || (kind === 'REVIEWER' && !revisionId)}>{busy ? '运行中…' : kind === 'REVIEWER' ? '请求独立复核' : '读取并解释当前候选'}</button>
    {kind === 'REVIEWER' && !revisionId && <p>先保存本次方案，再请求针对该修订的复核。</p>}
    {failure && <p className="inline-error" role="alert">{failure}{runId && <button onClick={() => { setFailure(null); setRetry(value => value + 1) }}>重新读取状态</button>}</p>}
    {run && <div className="agent-runtime" role="status"><strong>{run.error_code === 'MODEL_NOT_CONFIGURED' ? '模型尚未配置' : run.status === 'SUCCEEDED' ? '已生成结构化结果' : run.status === 'QUEUED' ? '已排队' : run.status === 'RUNNING' ? '正在读取与核对' : '本次运行未完成'}</strong>{run.error_code === 'MODEL_NOT_CONFIGURED' ? <p>模型服务未接入，本次没有执行模型推理或生成解读。密钥和模型名称可在后端配置后启用。</p> : run.error_code && <p>运行代码：{run.error_code}</p>}<ol>{run.tools.map(tool => <li key={tool.call_no}><span>{toolLabels[tool.tool_name] || '业务工具'}</span><small>{tool.status === 'SUCCEEDED' ? '读取完成' : tool.status === 'DENIED' ? '范围校验拒绝' : tool.status === 'STARTED' ? '读取中' : '读取失败'}</small></li>)}</ol></div>}
    {output && <div className="agent-output"><h4>{output.summary}</h4>{output.summary_source_refs?.length > 0 && <Sources refs={output.summary_source_refs} />}{[...output.statements, ...output.findings].map((item, index) => <article key={index}><p>{item.message}</p><Sources refs={item.source_refs} /></article>)}{output.proposed_facts.length > 0 && <><h4>文本中提取的待核对事实</h4><p>这些内容尚未成为已确认患者事实，不能直接用于排除或计算。</p>{output.proposed_facts.map((fact, i) => <article key={i}><strong>{fact.fact_code}：{String(fact.value ?? '未确定')}</strong><small>{({ NEGATED: '否定陈述', UNCERTAIN: '不确定陈述', CONFLICT: '存在冲突', ASSERTED: '待核实陈述' } as Record<string, string>)[fact.status]}</small><blockquote>{fact.quote}</blockquote><small>原文字符位置 {fact.start}—{fact.end}</small><Sources refs={fact.source_refs} /></article>)}</>}{output.pending_questions.length > 0 && <><h4>待确认</h4><ul>{output.pending_questions.map((item, index) => <li key={index}>{item}</li>)}</ul></>}</div>}
  </section>
}
