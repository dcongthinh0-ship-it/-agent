import type { FieldDefinition, MedicationItem, RegimenDetail } from './api'
import { matchField } from './blueprint-fields'
import './blueprint-document.css'

type Cell = { row: number; column: number; span: number; rowSpan: number; text: string; paragraphs?: Block[] }
type Block = Record<string, unknown>
export type MedicationDraft = { dose: string; route: string; frequency: string; day: string; note: string }
type MedicationKey = keyof MedicationDraft

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function number(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : fallback
}

function tableRows(block: Block): Cell[][] {
  const cellsByRow = new Map<number, Cell[]>()
  if (Array.isArray(block.cells)) {
    for (const item of block.cells) {
      if (!object(item)) continue
      const row = number(item.row, -1)
      const column = number(item.column, -1)
      if (row < 0 || column < 0) continue
      const cells = cellsByRow.get(row) ?? []
      cells.push({ row, column, span: Math.max(1, number(item.column_span, 1)), rowSpan: Math.max(1, number(item.row_span, 1)), text: text(item.text), paragraphs: Array.isArray(item.paragraphs) ? item.paragraphs.filter(object) : undefined })
      cellsByRow.set(row, cells)
    }
    const total = number(block.rows, cellsByRow.size)
    return Array.from({ length: total }, (_, row) => (cellsByRow.get(row) ?? []).sort((a, b) => a.column - b.column))
  }
  if (Array.isArray(block.rows)) {
    return block.rows.map((values, row) => Array.isArray(values)
      ? values.map((value, column) => ({ row, column, span: 1, rowSpan: 1, text: text(value) }))
      : [])
  }
  return []
}

function editable(field: FieldDefinition, mode: 'view' | 'edit'): boolean {
  return mode === 'edit' && field.edit_policy === 'RUNTIME_EDITABLE' && field.widget_type !== 'doctor-selector'
}

function Slot({ field, mode, value, onChange }: { field: FieldDefinition; mode: 'view' | 'edit'; value: string; onChange: (value: string) => void }) {
  if (!editable(field, mode)) {
    const pending = field.widget_type === 'doctor-selector' ? '等待院方人员数据' : field.edit_policy === 'RUNTIME_EDITABLE' ? '未填写' : field.source_type === 'CALCULATED' ? '待计算' : '等待患者数据'
    return <span className="blueprint-slot blueprint-slot-readonly" data-field-key={field.field_key}>{value || pending}</span>
  }
  const type = field.value_type === 'date' ? 'date' : field.value_type === 'time' ? 'time' : ['number', 'integer'].includes(field.value_type) ? 'number' : 'text'
  return <input
    className="blueprint-slot-input"
    aria-label={`${field.label}，本次方案`}
    data-field-key={field.field_key}
    type={type}
    min={['number', 'integer'].includes(field.value_type) ? '0' : undefined}
    step={field.value_type === 'integer' ? '1' : field.value_type === 'number' ? 'any' : undefined}
    value={value}
    onInput={event => onChange(event.currentTarget.value)}
    onChange={event => onChange(event.target.value)}
    placeholder="本次填写"
  />
}

function RichText({ block }: { block: Block }) {
  if (!Array.isArray(block.runs)) return <>{text(block.text)}</>
  return <>{block.runs.filter(object).map((run, index) => <span key={index} style={{ fontFamily: text(run.font) || undefined, fontSize: typeof run.size_pt === 'number' ? `${run.size_pt}pt` : undefined, fontWeight: run.bold ? 700 : undefined, fontStyle: run.italic ? 'italic' : undefined, textDecoration: run.underline && run.underline !== 'none' ? 'underline' : undefined, color: /^[0-9a-f]{6}$/i.test(text(run.color)) ? `#${text(run.color)}` : undefined }}>{text(run.text)}</span>)}</>
}

function FormText({ source, field, mode, value, onChange, block }: { source: string; field: FieldDefinition | null; mode: 'view' | 'edit'; value: string; onChange: (value: string) => void; block?: Block }) {
  return <span className="blueprint-form-text"><span className="blueprint-source-text">{block ? <RichText block={block} /> : source || '\u00a0'}</span>{field && <Slot field={field} mode={mode} value={value} onChange={onChange} />}</span>
}

function medicationAt(detail: RegimenDetail, blockIndex: number, row: number, headerRow: number, cells: Cell[]): MedicationItem | null {
  const rowSource = cells.map(cell => cell.text).join('').replace(/\s/g, '')
  return detail.medications.find(item => {
    const match = /^med_(\d+)_(\d+)$/.exec(item.item_key)
    const sourceMatch = /^med_source_\d+_(\d+)$/.exec(item.item_key)
    const atRow = (match && Number(match[1]) === blockIndex && Number(match[2]) === row)
      || (sourceMatch && row === headerRow + Number(sourceMatch[1]))
    return Boolean(atRow && rowSource.includes(item.source_drug_name.replace(/\s/g, '')))
  }) ?? null
}

function MedicationSlot({ item, label, fieldKey, mode, value, onChange }: { item: MedicationItem; label: string; fieldKey: MedicationKey; mode: 'view' | 'edit'; value: string; onChange: (value: string) => void }) {
  if (mode === 'view' || fieldKey === 'route' || fieldKey === 'frequency') return value ? <span className="blueprint-medication-value" data-medication-key={item.item_key} data-medication-field={fieldKey}>{value}</span> : null
  return <label className="blueprint-medication-control"><small>{label} · 本次</small><input
    aria-label={`${item.generic_name || item.source_drug_name}，${label}，本次方案`}
    data-medication-key={item.item_key}
    data-medication-field={fieldKey}
    value={value}
    onChange={event => onChange(event.target.value)}
    placeholder="医生填写"
  /></label>
}

export function BlueprintDocument({ detail, mode, values, medicationValues, onFieldChange, onMedicationChange }: { detail: RegimenDetail; mode: 'view' | 'edit'; values: Record<string, string>; medicationValues: Record<string, MedicationDraft>; onFieldChange: (key: string, value: string) => void; onMedicationChange: (itemKey: string, key: MedicationKey, value: string) => void }) {
  const tree = detail.word_layout || detail.document_tree
  const blocks = Array.isArray(tree.blocks)
    ? tree.blocks.filter(object)
    : []

  return <div className="blueprint-stage"><article className="blueprint-paper" aria-label={`${detail.display_name}完整方案表单`}>
    <div className="blueprint-paper-meta"><span>{detail.regimen_code} / V{detail.version_no}</span><span>{detail.word_layout ? 'Word 原表单' : '方案库表单'}</span></div>
    {blocks.map((block, index) => {
      if (block.kind === 'paragraph') {
        const raw = text(block.text)
        if (!raw.trim()) return <div className="blueprint-blank" key={index} aria-hidden="true" />
        const field = matchField(raw, detail.fields)
        const heading = !field && (text(block.style).toLowerCase().startsWith('heading') || text(block.alignment).toUpperCase().includes('CENTER'))
        const alignment = text(block.alignment).toLowerCase().includes('center') ? 'center' : text(block.alignment).toLowerCase().includes('right') ? 'right' : 'left'
        return heading
          ? <h3 className="blueprint-heading" key={index}><RichText block={block} /></h3>
          : <p style={{ textAlign: alignment }} className={`blueprint-paragraph ${field ? 'blueprint-field-paragraph' : ''}`} key={index}><FormText source={raw} field={field} mode={mode} block={block} value={field ? values[field.field_key] || '' : ''} onChange={value => field && onFieldChange(field.field_key, value)} /></p>
      }
      if (block.kind === 'table') {
        const rows = tableRows(block)
        if (!rows.length) return <p className="blueprint-unrendered" key={index}>该表格结构待核对</p>
        const headerRow = rows.findIndex(cells => cells.some(cell => ['实际使用剂量', '实际剂量'].includes(cell.text.trim())))
        const headers = headerRow >= 0 ? rows[headerRow] : []
        const actualDoseColumn = headers.find(cell => ['实际使用剂量', '实际剂量'].includes(cell.text.trim()))?.column
        const routeColumn = headers.find(cell => cell.text.trim() === '用药途径')?.column
        const timingColumn = headers.find(cell => ['用药频次及时间', '用药时间'].includes(cell.text.trim()))?.column
        const grid = Array.isArray(block.grid_twips) ? block.grid_twips.filter((v): v is number => typeof v === 'number') : []
        const totalWidth = grid.reduce((a, b) => a + b, 0)
        const sourceIndex = typeof block.blueprint_block_index === 'number' ? block.blueprint_block_index : index
        return <div className="blueprint-table-scroll" key={index}><table className="blueprint-table">{totalWidth > 0 && <colgroup>{grid.map((width, i) => <col key={i} style={{ width: `${width / totalWidth * 100}%` }} />)}</colgroup>}<tbody>{rows.map((cells, row) => <tr key={row} className={cells.length === 1 ? 'blueprint-table-section' : ''}>{cells.map(cell => {
          const field = matchField(cell.text, detail.fields)
          const medication = row > headerRow && headerRow >= 0 ? medicationAt(detail, sourceIndex, row, headerRow, cells) : null
          const draft = medication ? medicationValues[medication.item_key] : undefined
          const inMedicationColumn = medication && [actualDoseColumn, routeColumn, timingColumn].includes(cell.column)
          return <td key={`${cell.row}-${cell.column}`} colSpan={cell.span} rowSpan={cell.rowSpan} className={inMedicationColumn ? 'blueprint-medication-cell' : undefined}>
            {cell.paragraphs?.length && !field ? cell.paragraphs.map((p, i) => <div key={i} className="blueprint-cell-paragraph" style={{ textAlign: text(p.alignment) === 'center' ? 'center' : text(p.alignment) === 'right' ? 'right' : 'left', marginTop: typeof p.space_before_pt === 'number' ? `${p.space_before_pt}pt` : undefined, marginBottom: typeof p.space_after_pt === 'number' ? `${p.space_after_pt}pt` : undefined }}><RichText block={p} /></div>) : <FormText source={cell.text} field={field} mode={mode} block={cell.paragraphs?.length === 1 ? cell.paragraphs[0] : undefined} value={field ? values[field.field_key] || '' : ''} onChange={value => field && onFieldChange(field.field_key, value)} />}
            {medication && cell.column === actualDoseColumn && <MedicationSlot item={medication} label="实际使用剂量" fieldKey="dose" mode={mode} value={draft?.dose || ''} onChange={value => onMedicationChange(medication.item_key, 'dose', value)} />}
            {medication && cell.column === actualDoseColumn && (mode === 'edit' || draft?.note) && <MedicationSlot item={medication} label="用药备注" fieldKey="note" mode={mode} value={draft?.note || ''} onChange={value => onMedicationChange(medication.item_key, 'note', value)} />}
            {medication && cell.column === timingColumn && <MedicationSlot item={medication} label="给药日期" fieldKey="day" mode={mode} value={draft?.day || ''} onChange={value => onMedicationChange(medication.item_key, 'day', value)} />}
          </td>
        })}</tr>)}</tbody></table></div>
      }
      return <p className="blueprint-unrendered" key={index}>未识别的表单块：{text(block.kind) || '未知类型'}</p>
    })}
    <div className="blueprint-paper-foot">固定来源方案 · 查看与患者编辑使用同一份完整表单。</div>
  </article></div>
}
