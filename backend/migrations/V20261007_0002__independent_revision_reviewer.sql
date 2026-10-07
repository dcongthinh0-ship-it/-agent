-- A Reviewer may inspect an exact saved revision without fabricating a preceding main run.
DO $$
DECLARE old_definition text; new_definition text;
BEGIN
 SELECT pg_get_functiondef('ops.runtime_same_scope()'::regprocedure) INTO old_definition;
 new_definition=replace(old_definition,
  '(p.profile_kind=''REVIEWER'' AND NEW.review_target_agent_run_id IS NOT NULL)',
  '(p.profile_kind=''REVIEWER'' AND (NEW.review_target_agent_run_id IS NOT NULL OR EXISTS
    (SELECT 1 FROM clinical.decision_run rd WHERE rd.id=NEW.decision_run_id
     AND rd.purpose=''ASSESS_REVISION'' AND rd.target_revision_id IS NOT NULL)))');
 IF new_definition=old_definition THEN RAISE EXCEPTION 'unexpected existing scope guard; migration refused'; END IF;
 EXECUTE new_definition;
END $$;
