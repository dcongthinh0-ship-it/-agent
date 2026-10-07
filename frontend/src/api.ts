export interface CapabilityStatus {
  product: string
  mode: 'READ_ONLY_TEST' | 'WORKFLOW_TEST' | 'NOT_APPROVED'
  database: 'CONNECTED' | 'UNCONFIGURED' | 'UNAVAILABLE' | 'TEST_DOUBLE'
  model: 'NOT_CONNECTED' | 'CONFIGURED_NOT_VERIFIED'
  hospital: 'NOT_CONNECTED' | 'CONFIGURED_NOT_VERIFIED'
  patient_context: 'NOT_CONNECTED' | 'TEST_ONLY'
  clinical_release: 'NOT_ENABLED'
}

export interface RegimenSummary {
  regimen_id: string
  regimen_code: string
  display_name: string
  cancer_category: string | null
  version_id: string
  version_no: number
  version_status: string
  evidence_link_count: number
}

export interface RegimenPage {
  items: RegimenSummary[]
  total: number
  page: number
  page_size: number
}

export interface MedicationItem {
  item_key: string
  display_order: number
  source_drug_name: string
  generic_name: string | null
  standard_dose_text: string | null
  dose_unit: string | null
  dose_basis: string | null
  route_text: string | null
  frequency_text: string | null
  administration_day_text: string | null
}

export interface ContentBlock {
  section_code: string
  block_type: string
  display_order: number
  title: string | null
  raw_text: string | null
}

export interface FieldDefinition {
  field_key: string
  label: string
  value_type: string
  widget_type: string | null
  source_type: string
  edit_policy: string
  required: boolean
  repeatable: boolean
  display_order: number
  default_value: unknown
}

export interface RegimenDetail extends RegimenSummary {
  blueprint_schema_version: string | null
  document_tree: Record<string, unknown>
  fields: FieldDefinition[]
  medications: MedicationItem[]
  content_blocks: ContentBlock[]
  content_truncated: boolean
  word_layout?: Record<string, unknown> | null
  layout_verification?: Record<string, unknown> | null
}

export interface EvidenceSummary {
  association_id: string
  association_scope: string
  association_status: string
  evidence_id: string
  source_code: string
  display_source: string
  evidence_status: string
  source_title: string | null
  source_version: string | null
  source_status: string | null
  source_recommendation_raw: string | null
  source_evidence_category_raw: string | null
  internal_level: number | null
  evidence_grade: string | null
  grade_mapping_version: string | null
  excerpt_preview: string | null
}

export interface EvidenceList {
  regimen_version_id: string
  items: EvidenceSummary[]
  truncated: boolean
}

export interface EvidenceDetail extends Omit<EvidenceSummary, 'association_id' | 'association_scope' | 'association_status' | 'excerpt_preview'> {
  verbatim_excerpt: string | null
  source_locator: Record<string, unknown>
  disease_scope: Record<string, unknown>
  context_description: Record<string, unknown>
  source_url: string | null
  source_date: string | null
}

export interface PreparedCandidate {
  candidate_id: string
  regimen_id: string | null
  regimen_code: string | null
  display_name: string | null
  version_id: string | null
  version_status: string | null
  presentation_region: 'RECOMMENDATION' | 'X_EXCLUDED'
  rank_group: number | null
  evidence_state: string
  evidence_level: number | null
  evidence_grade: string | null
  data_labels: unknown[]
  safety_labels: unknown[]
  x_reason_code: string | null
}

export interface ContextReadout {
  context_id: string
  mode: 'TEST_ONLY'
  context_state: string
  expires_at: string
  prepare_run_id: string | null
  prepare_status: string | null
  prepare_stage: string | null
  prepare_error_code: string | null
  decision_run_id: string | null
  decision_status: string | null
  outcome_code: string | null
  patient_ref: string
  encounter_ref: string
  candidates: PreparedCandidate[]
  generation?: number
  snapshot_id?: string | null
  snapshot?: PatientSnapshot | null
  notices?: Finding[]
  instances?: { id: string; row_version: number; current_revision_id: string | null; current_confirmed_revision_id: string | null; read_only: boolean; display_name: string }[]
}

export interface SourceReference { namespace: string; id: string; version: string; content_hash: string }
export interface Finding { code: string; message: string; field_path?: string; source_refs?: SourceReference[] }
export interface PatientSnapshot { patient_ref: string; encounter_ref: string; captured_at: string; facts: Record<string, { value: unknown; status: string; unit?: string; observed_at?: string; source: SourceReference }>; text_records: unknown[] }
export interface PatientInstance {
  read_only: boolean
  instance_id: string; row_version: number; template: RegimenDetail; snapshot: PatientSnapshot
  field_values: Record<string, unknown>; medication_values: Record<string, { actual_dose_text: string; administration_day_text: string; instructions: string }>
  revision_id: string | null; revision_hash: string | null; confirmed_revision_id: string | null
  compilation_state: string; issues: Finding[]; data_labels: Finding[]; safety_labels: Finding[]
  calculations: Record<string, unknown>; bsa: Record<string, unknown> | null
  history: { id: string; revision_no: number; content_hash: string; created_at: string; change_reason: string | null }[]
}
export interface CandidateDetail { candidate_id: string; template: RegimenDetail; template_hash: string; snapshot: PatientSnapshot; field_values: Record<string, unknown>; assessment: Record<string, unknown> }
export interface AgentReadout {
  agent_run_id: string; kind: string; status: string; error_code: string | null; revision_id: string | null
  tools: { call_no: number; tool_name: string; status: string; error_code: string | null }[]
  outputs: { validation_state: string; structured_payload: { summary: string; summary_source_refs: SourceReference[]; statements: { message: string; source_refs: SourceReference[] }[]; findings: { code: string; message: string; severity: string; source_refs: SourceReference[] }[]; pending_questions: string[]; proposed_facts: { fact_code: string; value: unknown; status: string; quote: string; record_id: string; start: number; end: number; source_refs: SourceReference[] }[] } }[]
}

let accessToken: string | null = null
export function setHostToken(value: string | null) { accessToken = value }
export function hasHostToken() { return Boolean(accessToken) }

export class ApiFailure extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message)
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { signal, headers: { Accept: 'application/json', ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) } })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiFailure(0, 'NETWORK_ERROR', '连接中断，请检查本地服务后重试')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiFailure(response.status, payload?.code ?? 'REQUEST_FAILED', payload?.message ?? '读取失败，请稍后重试')
  }
  return response.json() as Promise<T>
}

export async function postJson<T>(path: string, body: unknown, key: string = crypto.randomUUID(), signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { method: 'POST', signal, headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': key, ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) }, body: JSON.stringify(body) })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiFailure(0, 'NETWORK_ERROR', '连接中断。请用本次操作继续重试，避免重复创建。')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiFailure(response.status, payload?.code ?? 'REQUEST_FAILED', payload?.message ?? '操作未完成，请重试')
  }
  return response.json() as Promise<T>
}
