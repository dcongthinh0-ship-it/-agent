-- Additive product migration. Existing catalog/evidence content and review states are untouched.
CREATE TABLE knowledge.regimen_applicability_version (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now(),
 created_by_principal text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 row_version bigint NOT NULL DEFAULT 1 CHECK(row_version>0),
 regimen_version_id uuid NOT NULL REFERENCES regimen_catalog.regimen_version(id),
 regimen_content_hash char(64) NOT NULL CHECK(regimen_content_hash ~ '^[0-9a-f]{64}$'),
 applicability_key text NOT NULL, version_no integer NOT NULL CHECK(version_no>0),
 relation_kind text NOT NULL CHECK(relation_kind IN ('DIRECT','RELATED_OFF_LABEL')),
 source_document_version_id uuid REFERENCES knowledge.source_document_version(id),
 evidence_record_version_id uuid REFERENCES knowledge.evidence_record_version(id),
 source_locator jsonb NOT NULL DEFAULT '{}', disease_code text NOT NULL,
 disease_system_version text NOT NULL, pathology_scope jsonb NOT NULL DEFAULT '{}',
 source_condition_text text NOT NULL, display_context jsonb NOT NULL DEFAULT '{}',
 phase0_policy jsonb NOT NULL, content_hash char(64) NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 review_event_id uuid, payload_schema_version text NOT NULL,
 UNIQUE(regimen_version_id,applicability_key,version_no)
);
CREATE INDEX ix_applicability_disease ON knowledge.regimen_applicability_version(disease_code,status);
CREATE TABLE knowledge.rule_package_version (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now(),
 created_by_principal text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 row_version bigint NOT NULL DEFAULT 1 CHECK(row_version>0), package_key text NOT NULL,
 version_no integer NOT NULL CHECK(version_no>0), clinical_spec_version text NOT NULL,
 source_document_version_id uuid NOT NULL REFERENCES knowledge.source_document_version(id),
 schema_version text NOT NULL, manifest jsonb NOT NULL, policy_payload jsonb NOT NULL,
 content_hash char(64) NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 published_at timestamptz, review_event_id uuid, UNIQUE(package_key,version_no)
);
CREATE TABLE knowledge.review_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now(),
 created_by_principal text NOT NULL, target_type text NOT NULL,
 regimen_version_id uuid REFERENCES regimen_catalog.regimen_version(id),
 component_id uuid REFERENCES regimen_catalog.form_component(id),
 source_document_version_id uuid REFERENCES knowledge.source_document_version(id),
 evidence_record_version_id uuid REFERENCES knowledge.evidence_record_version(id),
 regimen_evidence_link_id uuid REFERENCES knowledge.regimen_evidence_link(id),
 applicability_version_id uuid REFERENCES knowledge.regimen_applicability_version(id),
 rule_package_version_id uuid REFERENCES knowledge.rule_package_version(id),
 import_row_id uuid REFERENCES knowledge.import_row(id),
 action text NOT NULL CHECK(action IN ('SUBMIT','REVIEW_APPROVE','REVIEW_REJECT','PUBLISH','RETIRE')),
 from_state text NOT NULL, to_state text NOT NULL,
 reviewed_content_hash char(64) NOT NULL CHECK(reviewed_content_hash ~ '^[0-9a-f]{64}$'),
 actor_principal text NOT NULL, reason text, acted_at timestamptz NOT NULL DEFAULT now(),
 CHECK(num_nonnulls(regimen_version_id,component_id,source_document_version_id,
   evidence_record_version_id,regimen_evidence_link_id,applicability_version_id,
   rule_package_version_id,import_row_id)=1),
 CHECK(CASE target_type WHEN 'REGIMEN' THEN regimen_version_id IS NOT NULL
 WHEN 'COMPONENT' THEN component_id IS NOT NULL WHEN 'SOURCE' THEN source_document_version_id IS NOT NULL
 WHEN 'EVIDENCE' THEN evidence_record_version_id IS NOT NULL WHEN 'LINK' THEN regimen_evidence_link_id IS NOT NULL
 WHEN 'APPLICABILITY' THEN applicability_version_id IS NOT NULL WHEN 'RULE_PACKAGE' THEN rule_package_version_id IS NOT NULL
 WHEN 'IMPORT_ROW' THEN import_row_id IS NOT NULL ELSE false END),
 CHECK(action NOT IN ('REVIEW_REJECT','RETIRE') OR nullif(btrim(reason),'') IS NOT NULL)
);
CREATE INDEX ix_review_applicability ON knowledge.review_event(applicability_version_id,acted_at);
CREATE INDEX ix_review_rule ON knowledge.review_event(rule_package_version_id,acted_at);
ALTER TABLE knowledge.regimen_applicability_version ADD FOREIGN KEY(review_event_id)
 REFERENCES knowledge.review_event(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE knowledge.rule_package_version ADD FOREIGN KEY(review_event_id)
 REFERENCES knowledge.review_event(id) DEFERRABLE INITIALLY DEFERRED;
CREATE TRIGGER immutable_review_event BEFORE UPDATE OR DELETE ON knowledge.review_event
 FOR EACH ROW EXECUTE FUNCTION ops.runtime_write_policy('I');
CREATE FUNCTION knowledge.reviewed_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e knowledge.review_event;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'knowledge history cannot be deleted'; END IF;
 IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','review_event_id','published_at','updated_at','row_version'])
  IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','review_event_id','published_at','updated_at','row_version'])
 THEN RAISE EXCEPTION 'knowledge version content is immutable; create another version'; END IF;
 IF NEW.status<>'DRAFT' THEN
  SELECT * INTO e FROM knowledge.review_event WHERE id=NEW.review_event_id;
  IF e.id IS NULL OR e.reviewed_content_hash<>NEW.content_hash OR e.to_state<>NEW.status
   OR (TG_TABLE_NAME='regimen_applicability_version' AND e.applicability_version_id IS DISTINCT FROM NEW.id)
   OR (TG_TABLE_NAME='rule_package_version' AND e.rule_package_version_id IS DISTINCT FROM NEW.id)
  THEN RAISE EXCEPTION 'matching review event is required'; END IF;
 END IF;
 IF TG_OP='UPDATE' THEN NEW.row_version=OLD.row_version+1; NEW.updated_at=now(); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_applicability_version BEFORE INSERT OR UPDATE OR DELETE
 ON knowledge.regimen_applicability_version FOR EACH ROW EXECUTE FUNCTION knowledge.reviewed_version_guard();
CREATE TRIGGER guard_rule_package_version BEFORE INSERT OR UPDATE OR DELETE
 ON knowledge.rule_package_version FOR EACH ROW EXECUTE FUNCTION knowledge.reviewed_version_guard();

-- Word layout is an additional, verified rendering asset, not a replacement public blueprint.
CREATE TABLE catalog_bridge.form_layout_asset (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now(),
 created_by_principal text NOT NULL, regimen_version_id uuid NOT NULL REFERENCES regimen_catalog.regimen_version(id),
 source_artifact_hash char(64) NOT NULL CHECK(source_artifact_hash ~ '^[0-9a-f]{64}$'),
 blueprint_hash char(64) NOT NULL CHECK(blueprint_hash ~ '^[0-9a-f]{64}$'),
 schema_version text NOT NULL, content_hash char(64) NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 layout_payload jsonb NOT NULL, verification_report jsonb NOT NULL,
 UNIQUE(regimen_version_id,blueprint_hash,content_hash)
);
CREATE TRIGGER immutable_form_layout BEFORE UPDATE OR DELETE ON catalog_bridge.form_layout_asset
 FOR EACH ROW EXECUTE FUNCTION ops.runtime_write_policy('I');

-- Display the agreed levels in test reference mode without falsely marking them VERIFIED.
ALTER TABLE clinical.decision_candidate DROP CONSTRAINT ck_decision_candidate_evidence_state_enum;
ALTER TABLE clinical.decision_candidate ADD CONSTRAINT ck_decision_candidate_evidence_state_enum
 CHECK(evidence_state IN ('VERIFIED','UNVERIFIED','NO_APPROVED_EVIDENCE','TEST_REFERENCE'));
ALTER TABLE clinical.decision_candidate DROP CONSTRAINT ck_candidate_evidence;
ALTER TABLE clinical.decision_candidate ADD CONSTRAINT ck_candidate_evidence CHECK (
 (evidence_state NOT IN ('VERIFIED','TEST_REFERENCE') OR
 (evidence_level IS NOT NULL AND evidence_grade IS NOT NULL AND selected_evidence_ref IS NOT NULL)) AND
 (evidence_state IN ('VERIFIED','TEST_REFERENCE') OR
 (evidence_level IS NULL AND evidence_grade IS NULL AND rank_group IS NULL)) AND
 (rank_group IS NULL OR (presentation_region='RECOMMENDATION' AND evidence_state IN ('VERIFIED','TEST_REFERENCE')))
);
CREATE FUNCTION clinical.test_reference_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.evidence_state='TEST_REFERENCE' AND NOT EXISTS (
  SELECT 1 FROM clinical.decision_run WHERE id=NEW.decision_run_id AND usage_mode='TEST_ONLY')
 THEN RAISE EXCEPTION 'test reference is forbidden in clinical runs'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_test_reference BEFORE INSERT ON clinical.decision_candidate
 FOR EACH ROW EXECUTE FUNCTION clinical.test_reference_guard();
CREATE INDEX ix_job_claim ON ops.job(status,available_at,lease_expires_at) WHERE status IN ('QUEUED','RUNNING','RETRY_WAIT');
