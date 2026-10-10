import type { FieldDefinition } from '../../types/contracts'

function normalize(value: string): string {
  return value.toLowerCase().replace(/\([^)]*\)|（[^）]*）/g, '').replace(/[\s:：()（）_\-\/，、。·]/g, '')
}

export function matchField(source: string, fields: FieldDefinition[]): FieldDefinition | null {
  const separator = source.search(/[：:]/)
  if (separator < 0) return null
  const candidate = normalize(source.slice(0, separator))
  const remainder = source.slice(separator + 1).replace(/[\s_＿—-]/g, '')
  // A populated instruction or checkbox after the label is template text, not a patient value.
  if (!candidate) return null
  const field = [...fields].sort((a, b) => b.label.length - a.label.length).find(field => {
    const label = normalize(field.label)
    return label.length >= 2 && (candidate === label || candidate === normalize(field.field_key))
  }) ?? null
  // Cycle guidance remains visible alongside the patient's editable total.
  if (remainder && !/^[年月日时分秒%％.0-9]*$/.test(remainder) && field?.field_key !== 'total_cycles') return null
  return field
}

