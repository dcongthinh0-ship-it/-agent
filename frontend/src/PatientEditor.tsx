import { useMemo, useState } from 'react'
import type { FieldDefinition, MedicationItem, RegimenDetail } from './api'
import { BlueprintDocument, type MedicationDraft } from './BlueprintDocument'
import './patient-editor.css'

type Section = 'document' | 'context' | 'plan' | 'medications' | 'team' | 'review'

const sections: { id: Section; label: string; caption: string }[] = [
  { id: 'document', label: '完整方案表单', caption: '方案库原始布局' },
  { id: 'context', label: '患者与就诊', caption: '院方来源' },
  { id: 'plan', label: '治疗安排', caption: '医生填写' },
  { id: 'medications', label: '主治疗药品', caption: '逐行核对' },
  { id: 'team', label: '医师团队', caption: '身份待接入' },
  { id: 'review', label: '草稿预览', caption: '未保存' },
]

const emptyMedication = (): MedicationDraft => ({ dose: '', route: '', frequency: '', day: '', note: '' })
const isTeamField = (field: FieldDefinition) => field.widget_type === 'doctor-selector' || field.field_key.endsWith('_physician')
const isEditable = (field: FieldDefinition) => field.edit_policy === 'RUNTIME_EDITABLE' && !isTeamField(field)

function FieldControl({ field, value, onChange, mode }: { field: FieldDefinition; value: string; onChange: (value: string) => void; mode: 'view' | 'edit' }) {
  const editable = mode === 'edit' && isEditable(field)
  const type = field.value_type === 'date' ? 'date' : field.value_type === 'time' ? 'time' : ['number', 'integer'].includes(field.value_type) ? 'number' : 'text'
  return <label className={`editor-field ${editable ? '' : 'editor-field-locked'}`}>
    <span className="editor-field-label">{field.label}{field.required && <em>必填</em>}</span>
    {editable ? <input
      type={type}
      step={field.value_type === 'integer' ? '1' : field.value_type === 'number' ? 'any' : undefined}
      min={['number', 'integer'].includes(field.value_type) ? '0' : undefined}
      value={value}
      onInput={event => onChange(event.currentTarget.value)}
      onChange={event => onChange(event.target.value)}
      placeholder="待医生填写"
    /> : <span className="editor-unbound">{isEditable(field) ? '待本次填写' : '待院方数据接入'}</span>}
    <small>{editable ? '医生本次填写 · 尚未保存' : isEditable(field) ? '只读查看 · 尚无患者方案值' : field.source_type === 'COMPUTED' ? '受控计算结果 · 未接入' : '院方来源 · 当前不可填写'}</small>
  </label>
}

function MedicationCard({ item, index, draft, onChange, mode }: { item: MedicationItem; index: number; draft: MedicationDraft; onChange: (next: MedicationDraft) => void; mode: 'view' | 'edit' }) {
  const update = (key: keyof MedicationDraft, value: string) => onChange({ ...draft, [key]: value })
  return <article className="editor-medication">
    <div className="editor-medication-head"><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{item.generic_name || item.source_drug_name}</strong><small>方案药品行 {item.item_key}</small></div><span className="editor-medication-status">未形成医嘱</span></div>
    <div className="template-reference"><span>固定版本参考</span><p>剂量：{item.standard_dose_text || '未结构化'}　·　途径：{item.route_text || '未配置'}　·　频次：{item.frequency_text || '未配置'}　·　日期：{item.administration_day_text || '未配置'}</p></div>
    {mode === 'view' ? <div className="editor-unbound medication-unbound">患者方案值尚未建立。查看固定版本参考不等于采用剂量或生成医嘱。</div> : <div className="editor-medication-grid">
      <label><span>本次拟定剂量 <em>医生核对</em></span><input value={draft.dose} onChange={event => update('dose', event.target.value)} placeholder="填写数值与单位，不自动采用标准剂量" /></label>
      <label><span>给药途径</span><input value={draft.route} onChange={event => update('route', event.target.value)} placeholder="待院方字典映射" /></label>
      <label><span>给药频次</span><input value={draft.frequency} onChange={event => update('frequency', event.target.value)} placeholder="待院方字典映射" /></label>
      <label><span>给药日期</span><input value={draft.day} onChange={event => update('day', event.target.value)} placeholder="例如 D1；需核对治疗锚点" /></label>
      <label className="editor-medication-note"><span>本次调整说明</span><input value={draft.note} onChange={event => update('note', event.target.value)} placeholder="记录与固定版本不同的原因" /></label>
    </div>}
  </article>
}

export function PatientEditor({ detail, mode, onClose, onEdit }: { detail: RegimenDetail; mode: 'view' | 'edit'; onClose: () => void; onEdit?: () => void }) {
  const [section, setSection] = useState<Section>('document')
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({})
  const [medicationValues, setMedicationValues] = useState<Record<string, MedicationDraft>>({})
  const [leavePrompt, setLeavePrompt] = useState(false)

  const contextFields = detail.fields.filter(field => !isTeamField(field) && !isEditable(field))
  const planFields = detail.fields.filter(field => isEditable(field))
  const teamFields = detail.fields.filter(isTeamField)
  const modifiedFields = detail.fields.filter(field => Boolean(fieldValues[field.field_key]?.trim()))
  const modifiedMedications = detail.medications.filter(item => Object.values(medicationValues[item.item_key] || emptyMedication()).some(value => value.trim()))
  const changeCount = modifiedFields.length + modifiedMedications.length
  const issues = useMemo(() => [
    '尚无患者、就诊、医生身份与快照，无法创建患者方案实例。',
    detail.version_status === 'DRAFT' ? '固定版本仍为草稿，不能作为已发布的临床模板。' : null,
    '院方药品、途径、频次和日期编码尚未映射，医嘱未编译。',
    detail.medications.some(item => !item.frequency_text) ? '原方案存在未配置频次的药品行，需逐行核对。' : null,
  ].filter((issue): issue is string => Boolean(issue)), [detail])

  const close = () => mode === 'edit' && changeCount ? setLeavePrompt(true) : onClose()
  const updateMedication = (key: string, next: MedicationDraft) => setMedicationValues(current => ({ ...current, [key]: next }))
  const updateMedicationField = (itemKey: string, key: keyof MedicationDraft, value: string) => setMedicationValues(current => ({ ...current, [itemKey]: { ...emptyMedication(), ...current[itemKey], [key]: value } }))

  return <div className="patient-editor" role="dialog" aria-modal="true" aria-label={mode === 'view' ? '方案表单只读查看' : '患者方案编辑预览'}>
    <header className="editor-topbar"><div><span className="editor-kicker">PATIENT REGIMEN / {mode === 'view' ? 'VIEW' : 'EDIT PREVIEW'}</span><h2>{mode === 'view' ? '方案表单查看' : '患者方案编辑'} <span>未绑定患者 · {mode === 'view' ? '只读' : '仅本次预览'}</span></h2></div><button className="editor-close" onClick={close}>返回方案浏览</button></header>
    <div className="editor-template"><div><span>固定来源</span><strong>{detail.regimen_code} · {detail.display_name}</strong></div><div><span>方案版本</span><strong>V{detail.version_no} · {detail.version_status === 'DRAFT' ? '草稿' : detail.version_status}</strong></div><div><span>患者 / 就诊</span><strong>未接入 / 未接入</strong></div></div>
    <div className="editor-body">
      <nav className="editor-sections" aria-label="方案表单章节"><p>表单章节</p>{sections.filter(item => mode === 'edit' || item.id !== 'review').map(item => <button key={item.id} className={section === item.id ? 'selected' : ''} onClick={() => setSection(item.id)}><strong>{item.label}</strong><small>{item.caption}</small></button>)}</nav>
      <main className="editor-main">
        {section === 'document' && <><div className="editor-section-heading"><span>01 / 固定版本表单</span><h3>完整方案表单</h3><p>{mode === 'view' ? '按方案库蓝图原顺序查看段落与表格；当前没有患者实例，患者值不作虚构。' : '在原表单中填写允许医生修改的字段和已定位的主药行。修改仅在本页预览，尚未建立患者方案修订。'}</p></div><BlueprintDocument detail={detail} mode={mode} values={fieldValues} medicationValues={medicationValues} onFieldChange={(key, value) => setFieldValues(current => ({ ...current, [key]: value }))} onMedicationChange={updateMedicationField} /></>}
        {section === 'context' && <><div className="editor-section-heading"><span>01 / 来源信息</span><h3>患者与就诊</h3><p>这些字段应从院方接口和冻结快照读取。当前未接入，不允许在这里手填成已核验事实。</p></div><div className="editor-fields">{contextFields.map(field => <FieldControl key={field.field_key} field={field} mode={mode} value="" onChange={() => {}} />)}</div></>}
        {section === 'plan' && <><div className="editor-section-heading"><span>02 / 医生填写</span><h3>治疗安排</h3><p>{mode === 'view' ? '同一份表单以只读模式展示；尚未创建患者实例，因此本次值为空。' : '字段来自当前固定版本。输入只在当前页面内存中预览，离开后丢弃。'}</p></div><div className="editor-fields">{planFields.map(field => <FieldControl key={field.field_key} field={field} mode={mode} value={fieldValues[field.field_key] || ''} onChange={value => setFieldValues(current => ({ ...current, [field.field_key]: value }))} />)}</div></>}
        {section === 'medications' && <><div className="editor-section-heading"><span>03 / 结构化药品行</span><h3>主治疗药品</h3><p>固定版本提供参考；本次剂量、频次和日期须由医生核对。此处不计算标准剂量，也不生成可交付医嘱。</p></div>{detail.medications.length ? detail.medications.map((item, index) => <MedicationCard key={item.item_key} item={item} index={index} mode={mode} draft={medicationValues[item.item_key] || emptyMedication()} onChange={next => updateMedication(item.item_key, next)} />) : <p className="editor-empty">该版本没有结构化主治疗药品行，不能从原文猜补。</p>}</>}
        {section === 'team' && <><div className="editor-section-heading"><span>04 / 医师身份</span><h3>医师团队</h3><p>医师角色来自复用组件。未接院方医师目录和可信身份前，不把手输姓名当作签名身份。</p></div><div className="editor-fields">{teamFields.map(field => <FieldControl key={field.field_key} field={field} mode={mode} value="" onChange={() => {}} />)}</div></>}
        {section === 'review' && <><div className="editor-section-heading"><span>05 / 当前内存草稿</span><h3>修改预览</h3><p>以下仅显示本次页面修改；未写入患者方案实例或修订。</p></div>{changeCount === 0 ? <p className="editor-empty">尚未填写任何本次方案值。</p> : <div className="editor-review"><h4>字段修改 · {modifiedFields.length}</h4>{modifiedFields.map(field => <div key={field.field_key}><span>{field.label}</span><strong>{fieldValues[field.field_key]}</strong></div>)}<h4>药品行修改 · {modifiedMedications.length}</h4>{modifiedMedications.map(item => <div key={item.item_key}><span>{item.generic_name || item.source_drug_name}</span><strong>{Object.entries(medicationValues[item.item_key] || {}).filter(([, value]) => value).map(([key, value]) => `${({ dose: '剂量', route: '途径', frequency: '频次', day: '日期', note: '说明' } as Record<string, string>)[key]}：${value}`).join('；')}</strong></div>)}</div>}</>}
      </main>
      <aside className="editor-issues"><p className="editor-issues-kicker">待解决项 <span>{issues.length}</span></p><h3>尚不能保存或确认</h3><div>{issues.map((issue, index) => <p key={issue}><span>{String(index + 1).padStart(2, '0')}</span>{issue}</p>)}</div><small>规则、剂量计算和独立 Reviewer 尚未接入；当前提示不等于临床评估结果。</small></aside>
    </div>
    <footer className="editor-footer"><div><strong>{mode === 'view' ? '只读表单' : changeCount ? `${changeCount} 处页面修改` : '尚无页面修改'}</strong><span>{mode === 'view' ? '固定版本参考 · 尚无患者方案实例' : '仅当前内存 · 未保存为患者修订'}</span></div><div className="editor-footer-actions">{mode === 'view' ? onEdit && <button className="editor-primary-action" onClick={onEdit}>进入编辑预览</button> : <><button onClick={() => { setFieldValues({}); setMedicationValues({}); setSection('document') }} disabled={!changeCount}>清空本次修改</button><button onClick={() => setSection('review')}>预览修改</button><button disabled title="需真实患者、就诊、快照与可信医生身份">保存患者方案 · 未接入</button><button disabled title="需先保存确切修订并通过受控校验">确认 · 未接入</button></>}</div></footer>
    {leavePrompt && <div className="editor-dialog-backdrop"><div className="editor-leave-dialog" role="alertdialog" aria-modal="true" aria-labelledby="leave-heading"><h3 id="leave-heading">本次修改尚未保存</h3><p>当前仅在页面内存中预览。返回后这些内容会丢失。</p><div><button onClick={() => setLeavePrompt(false)}>继续编辑</button><button className="danger" onClick={onClose}>放弃并返回</button></div></div></div>}
  </div>
}
