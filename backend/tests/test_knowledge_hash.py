from chemo_agent_product.domain import fingerprint
from chemo_agent_product.knowledge import evidence_link_matches


def test_import_contract_checks_blueprint_and_ordered_drug_composition():
    blueprint = {"blocks": [{"text": "source form"}]}
    meds = [{"name": "A", "dose": "10mg", "day": "D1", "drug_concept_id": "a"}]
    link = {
        "hash_contract_version": "blueprint-and-medication-json-v1",
        "regimen_content_hash": fingerprint({"blueprint": blueprint, "medications": meds}),
        "status": "DRAFT",
    }
    assert evidence_link_matches(link, blueprint, meds)
    assert not evidence_link_matches(link, {"blocks": []}, meds)
    assert not evidence_link_matches(link, blueprint, [{**meds[0], "dose": "20mg"}])
    assert link["status"] == "DRAFT"


def test_unknown_hash_contract_is_not_accepted():
    blueprint = {"text": "source"}
    link = {"hash_contract_version": "unknown", "regimen_content_hash": fingerprint(blueprint)}
    assert not evidence_link_matches(link, blueprint, [])
