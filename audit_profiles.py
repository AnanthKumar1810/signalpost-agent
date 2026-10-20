"""Audit JSONL profile lineage and summarize observed coverage without guessing truth."""
import argparse
import collections
import json
from pathlib import Path


SOURCE_FAMILIES = {
    "company_details": {"registry"}, "locations": {"registry"},
    "people": {"roles"}, "financials": {"financials"},
    "web_presence": {"website"}, "jobs": {"jobs"},
    "public_activity": {"public_activity"},
}


def audit(path: Path) -> dict:
    counts = collections.Counter()
    statuses = collections.defaultdict(collections.Counter)
    fields = collections.Counter()
    errors = collections.Counter()
    violations = []
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            profile = json.loads(line)
            org = profile.get("organisation_number")
            counts["profiles"] += 1
            if org in seen:
                violations.append(f"line {line_number}: duplicate organisation number {org}")
            seen.add(org)
            if profile.get("identity_anchor", {}).get("organisation_number") != org:
                violations.append(f"line {line_number}: identity number mismatch")
            if profile.get("identity_anchor", {}).get("status") == "verified":
                counts["verified_identities"] += 1
            snapshots = {item.get("id"): item for item in profile.get("source_snapshots", [])}
            evidence = {item.get("id"): item for item in profile.get("evidence", [])}
            for error in profile.get("errors", []):
                errors[error.get("type", "unknown")] += 1
            for claim in profile.get("claims", []):
                family = claim.get("family")
                statuses[family][claim.get("status")] += 1
                for fact in claim.get("facts", []):
                    counts["facts"] += 1
                    fields[fact.get("field")] += 1
                    ids = fact.get("evidence_ids")
                    if not isinstance(ids, list) or not ids:
                        violations.append(f"line {line_number}: {family} fact without evidence")
                        continue
                    for evidence_id in ids:
                        snapshot = snapshots.get(evidence_id)
                        item = evidence.get(evidence_id)
                        if not snapshot or not item:
                            violations.append(f"line {line_number}: missing evidence or snapshot {evidence_id}")
                        elif (snapshot.get("organisation_number") != org or
                              snapshot.get("family") not in SOURCE_FAMILIES.get(family, set()) or
                              item.get("source_id") != evidence_id or
                              not ({fact.get("field"), "public_activity" if fact.get("field") == "company_activity" else fact.get("field")}
                                   & set(item.get("claim_fields", []))) or
                              (fact.get("source_url") and fact["source_url"] != snapshot.get("url"))):
                            violations.append(f"line {line_number}: mismatched evidence {evidence_id}")
    return {"counts": dict(counts), "family_statuses": {k: dict(v) for k, v in statuses.items()},
            "fact_fields": dict(fields), "error_types": dict(errors),
            "lineage_violations": len(violations), "violation_examples": violations[:10]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.jsonl), ensure_ascii=False, indent=2))
