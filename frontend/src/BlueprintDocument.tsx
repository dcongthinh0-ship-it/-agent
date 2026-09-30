import type { FieldDefinition, MedicationItem, RegimenDetail } from './api'
import './blueprint-document.css'

type Cell = { row: number; column: number; span: number; text: string }
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

function collapse(cells: Cell[]): Cell[] {
  const result: Cell[] = []
  for (const cell of cells.sort((a, b) => a.column - b.column)) {
    const previous = result.at(-1)
    if (previous && previous.text === cell.text && previous.column + previous.span === cell.column) {
      previous.span += cell.span
    } else {
      result.push({ ...cell })
    }
  }
  return result
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
      cells.push({ row, column, span: Math.max(1, number(item.column_span, 1)), text: text(item.text) })
      cellsByRow.set(row, cells)
    }
    const total = number(block.rows, cellsByRow.size)
    return Array.from({ length: total }, (_, row) => collapse(cellsByRow.get(row) ?? []))
  }
  if (Array.isArray(block.rows)) {
    return block.rows.map((values, row) => Array.isArray(values)
      ? collapse(values.map((value, column) => ({ row, column, span: 1, text: text(value) })))
      : [])
  }
  return []
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/\([^)]*\)|（[^）]*）/g, '').replace(/[\s:：()（）_\-\/，、。·]/g, '')
}

function matchField(source: string, fields: FieldDefinition[]): FieldDefinition | null {
  const separator = source.search(/[：:]/)
  if (separator < 0) return null
  const candidate = normalize(source.slice(0, separator))
  const remainder = source.slice(separator + 1).replace(/[\s_＿—-]/g, '')
  // A populated instruction or checkbox after the label is template text, not a patient value.
  if (!candidate || (remainder && !/^[年月日时分秒%％.0-9]*$/.test(remainder))) return null
  return [...fields].sort((a, b) => b.label.length - a.label.length).find(field => {
    const label = normalize(field.label)
    return label.length >= 2 && candidate === label
  }) ?? null
}

function editable(field: FieldDefinition, mode: 'view' | 'edit'): boolean {
  return mode === 'edit' && field.edit_policy === 'RUNTIME_EDITABLE' && field.widget_type !== 'doctor-selector'
}

function Slot({ field, mode, value, onChange }: { field: FieldDefinition; mode: 'view' | 'edit'; value: string; onChange: (value: string) => void }) {
  if (!editable(field, mode)) {
    const pending = field.widget_type === 'doctor-selector' ? '待院方身份接入' : field.edit_policy === 'RUNTIME_EDITABLE' ? '待本次填写' : '待院方/计算接入'
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

function FormText({ source, field, mode, value, onChange }: { source: string; field: FieldDefinition | null; mode: 'view' | 'edit'; value: string; onChange: (value: string) => void }) {
  return <span className="blueprint-form-text"><span className="blueprint-source-text">{source || '\u00a0'}</span>{field && <Slot field={field} mode={mode} value={value} onChange={onChange} />}</span>
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
  if (mode === 'view') return <span className="blueprint-medication-value" data-medication-key={item.item_key} data-medication-field={fieldKey}>{value || '患者值未建立'}</span>
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
  const blocks = Array.isArray(detail.document_tree.blocks)
    ? detail.document_tree.blocks.filter(object)
    : []

  return <div className="blueprint-stage"><article className="blueprint-paper" aria-label={`${detail.display_name}完整方案表单`}>
    <div className="blueprint-paper-meta"><span>固定版本 {detail.regimen_code} / V{detail.version_no}</span><span>原始表单布局 · {blocks.length} 块</span></div>
    {blocks.map((block, index) => {
      if (block.kind === 'paragraph') {
        const raw = text(block.text)
        if (!raw.trim()) return <div className="blueprint-blank" key={index} aria-hidden="true" />
        const field = matchField(raw, detail.fields)
        const heading = !field && (text(block.style).toLowerCase().startsWith('heading') || text(block.alignment).toUpperCase().includes('CENTER'))
        return heading
          ? <h3 className="blueprint-heading" key={index}>{raw}</h3>
          : <p className={`blueprint-paragraph ${field ? 'blueprint-field-paragraph' : ''}`} key={index}><FormText source={raw} field={field} mode={mode} value={field ? values[field.field_key] || '' : ''} onChange={value => field && onFieldChange(field.field_key, value)} /></p>
      }
      if (block.kind === 'table') {
        const rows = tableRows(block)
        if (!rows.length) return <p className="blueprint-unrendered" key={index}>该表格结构待核对</p>
        const headerRow = rows.findIndex(cells => cells.some(cell => ['实际使用剂量', '实际剂量'].includes(cell.text.trim())))
        const headers = headerRow >= 0 ? rows[headerRow] : []
        const actualDoseColumn = headers.find(cell => ['实际使用剂量', '实际剂量'].includes(cell.text.trim()))?.column
        const routeColumn = headers.find(cell => cell.text.trim() === '用药途径')?.column
        const timingColumn = headers.find(cell => ['用药频次及时间', '用药时间'].includes(cell.text.trim()))?.column
        return <div className="blueprint-table-scroll" key={index}><table className="blueprint-table"><tbody>{rows.map((cells, row) => <tr key={row} className={cells.length === 1 ? 'blueprint-table-section' : ''}>{cells.map(cell => {
          const field = matchField(cell.text, detail.fields)
          const medication = row > headerRow && headerRow >= 0 ? medicationAt(detail, index, row, headerRow, cells) : null
          const draft = medication ? medicationValues[medication.item_key] : undefined
          const inMedicationColumn = medication && [actualDoseColumn, routeColumn, timingColumn].includes(cell.column)
          return <td key={`${cell.row}-${cell.column}`} colSpan={cell.span} className={inMedicationColumn ? 'blueprint-medication-cell' : undefined}>
            <FormText source={cell.text} field={field} mode={mode} value={field ? values[field.field_key] || '' : ''} onChange={value => field && onFieldChange(field.field_key, value)} />
            {medication && cell.column === actualDoseColumn && <MedicationSlot item={medication} label="实际使用剂量" fieldKey="dose" mode={mode} value={draft?.dose || ''} onChange={value => onMedicationChange(medication.item_key, 'dose', value)} />}
            {medication && cell.column === routeColumn && <MedicationSlot item={medication} label="给药途径" fieldKey="route" mode={mode} value={draft?.route || ''} onChange={value => onMedicationChange(medication.item_key, 'route', value)} />}
            {medication && cell.column === timingColumn && <><MedicationSlot item={medication} label="给药频次" fieldKey="frequency" mode={mode} value={draft?.frequency || ''} onChange={value => onMedicationChange(medication.item_key, 'frequency', value)} /><MedicationSlot item={medication} label="给药日期" fieldKey="day" mode={mode} value={draft?.day || ''} onChange={value => onMedicationChange(medication.item_key, 'day', value)} /></>}
          </td>
        })}</tr>)}</tbody></table></div>
      }
      return <p className="blueprint-unrendered" key={index}>未识别的表单块：{text(block.kind) || '未知类型'}</p>
    })}
    <div className="blueprint-paper-foot">本页根据固定版本的 document_tree 原顺序呈现。没有字段定义的内容仅显示原文，不自动变成患者填写项。</div>
  </article></div>
}
