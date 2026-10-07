-- Host switches and preparation retries have separate generation counters.
-- Nullable client_generation in the API preserves existing callers; the server
-- assigns the next host_generation under a session lock for those callers.
ALTER TABLE clinical.launch_context ADD COLUMN host_generation bigint NOT NULL DEFAULT 1;
ALTER TABLE clinical.launch_context ADD CONSTRAINT ck_launch_context_host_generation
  CHECK (host_generation > 0);
CREATE INDEX ix_launch_context_host_session_generation
  ON clinical.launch_context (hospital_id, operator_staff_id, session_scope_ref, host_generation DESC);
COMMENT ON COLUMN clinical.launch_context.host_generation IS
  'Monotonic patient-switch generation within one trusted host session. Independent of active_generation, which tracks preparation retries.';
