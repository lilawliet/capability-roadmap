#!/usr/bin/env python3
"""Read-only validation of capability-roadmap schemaVersion 1 (Python stdlib)."""
import argparse
from datetime import date
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


class Validator:
    def __init__(self, data, repo):
        self.data = data
        self.repo = repo
        self.errors = []
        self.warnings = []
        self.example = False
        self.git_refs = set()
        self.code_paths = []

    def error(self, where, message):
        self.errors.append(f"{where}: {message}")

    def obj(self, value, where):
        if not isinstance(value, dict):
            self.error(where, "expected object")
            return {}
        return value

    def required(self, obj, fields, where):
        for field in fields:
            if field not in obj:
                self.error(where, f"missing required field {field}")

    def text(self, value, where, nullable=False):
        if value is None and nullable:
            return None
        if not isinstance(value, str) or not value.strip():
            self.error(where, "expected nonempty string" + (" or null" if nullable else ""))
            return ""
        return value

    def array(self, value, where):
        if not isinstance(value, list):
            self.error(where, "expected array")
            return []
        return value

    def strings(self, obj, field, where, nonempty=False):
        values = self.array(obj.get(field), f"{where}.{field}")
        result = [self.text(v, f"{where}.{field}[{i}]") for i, v in enumerate(values)]
        if nonempty and not result:
            self.error(f"{where}.{field}", "must not be empty")
        return result

    def choice(self, obj, field, choices, where):
        value = obj.get(field)
        if not isinstance(value, str) or value not in choices.split("/"):
            self.error(f"{where}.{field}", f"expected one of {choices}")
            return ""
        return value

    def records(self, field):
        records = {}
        for i, raw in enumerate(self.array(self.data.get(field), field)):
            where = f"{field}[{i}]"
            item = self.obj(raw, where)
            identifier = self.text(item.get("id"), f"{where}.id")
            if identifier in records:
                self.error(where, f"duplicate ID {identifier}")
            if identifier:
                records[identifier] = item
        return records

    def refs(self, obj, field, targets, where, nonempty=False):
        identifiers = self.strings(obj, field, where, nonempty)
        if len(set(identifiers)) != len(identifiers):
            self.error(f"{where}.{field}", "duplicate references")
        for identifier in identifiers:
            if identifier not in targets:
                self.error(f"{where}.{field}", f"unknown reference {identifier!r}")
        return identifiers

    def iso_date(self, value, where, nullable=False):
        value = self.text(value, where, nullable)
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                self.error(where, "expected ISO date YYYY-MM-DD")
        return value

    def code_ref(self, value, where, nullable=False):
        value = self.text(value, where, nullable)
        if value:
            if not self.example and not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
                self.error(where, "expected full Git SHA (40 or 64 hexadecimal characters)")
            self.git_refs.add(value)
        return value

    def check_cycle(self, records, field, where):
        done, active = set(), set()
        for root in records:
            stack = [(root, False)]
            while stack:
                identifier, leaving = stack.pop()
                if leaving:
                    active.discard(identifier)
                    done.add(identifier)
                    continue
                if identifier in active:
                    self.error(where, f"dependency cycle involving {identifier}")
                    continue
                if identifier in done:
                    continue
                active.add(identifier)
                stack.append((identifier, True))
                deps = records[identifier].get(field, [])
                for target in reversed(deps) if isinstance(deps, list) else []:
                    if isinstance(target, str) and target in records:
                        stack.append((target, False))

    def assessment(self, capability, where, sources):
        record = self.obj(capability.get("assessment"), f"{where}.assessment")
        loc = f"{where}.assessment"
        self.required(record, ("reviewedAt", "codeRef"), loc)
        impl = self.choice(record, "implementation", "not_assessed/not_implemented/stub/reusable/partial/implemented", loc)
        validation = self.choice(record, "validation", "not_run/partial/passed/failed", loc)
        deployed = self.choice(record, "deployment", "unknown/not_deployed/deployed", loc)
        accepted = self.choice(record, "acceptance", "not_run/passed/failed", loc)
        fresh = self.choice(record, "freshness", "current/stale", loc)
        self.strings(record, "present", loc)
        self.strings(record, "gaps", loc)
        evidence = self.refs(record, "evidenceIds", sources, loc)
        kinds = {sources[e].get("kind") for e in evidence if e in sources and isinstance(sources[e].get("kind"), str)}
        self.iso_date(record.get("reviewedAt"), f"{loc}.reviewedAt", impl == "not_assessed")
        self.code_ref(record.get("codeRef"), f"{loc}.codeRef", impl == "not_assessed")
        if impl and impl != "not_assessed" and not kinds.intersection({"code", "inspection"}):
            self.error(loc, "implementation assessment needs code or inspection evidence")
        if validation and validation != "not_run" and not kinds.intersection({"test", "manual"}):
            self.error(loc, "executed validation needs test or manual evidence")
        if deployed == "deployed" and "deployment" not in kinds:
            self.error(loc, "deployed state needs deployment evidence")
        if accepted and accepted != "not_run" and "manual" not in kinds:
            self.error(loc, "executed acceptance needs manual evidence")
        if impl == "not_assessed" or fresh == "stale":
            self.warnings.append(f"{where}: {impl}, freshness={fresh}; not a current completion claim")
        return record

    def validate(self):
        self.data = self.obj(self.data, "document")
        if type(self.data.get("schemaVersion")) is not int or self.data["schemaVersion"] != 1:
            self.error("schemaVersion", "expected integer 1")
        project = self.obj(self.data.get("project"), "project")
        self.text(project.get("name"), "project.name")
        if type(project.get("example")) is not bool:
            self.error("project.example", "expected boolean")
        self.example = project.get("example") is True
        if self.example:
            self.warnings.append("teaching example only; these records are not project evidence")
            if self.repo:
                self.error("--repo", "cannot be combined with project.example=true")
        sources, decisions = self.records("sources"), self.records("decisions")
        capabilities, phases = self.records("capabilities"), self.records("phases")
        releases = self.records("releases")
        review = self.obj(self.data.get("review"), "review")
        self.required(review, ("from", "to"), "review")
        mode = self.choice(review, "mode", "initial/incremental/planning", "review")
        self.code_ref(review.get("from"), "review.from", mode != "incremental")
        end = self.code_ref(review.get("to"), "review.to", mode != "incremental")
        reviewed_date = self.iso_date(review.get("date"), "review.date")
        self.text(review.get("scope"), "review.scope")
        self.strings(review, "excluded", "review")
        reviewed = self.refs(review, "reviewedCapabilityIds", capabilities, "review")
        for identifier, source in sources.items():
            kind = self.choice(source, "kind", "requirement/decision/prototype/code/inspection/test/manual/deployment/example", identifier)
            for field in ("locator", "revision", "summary"):
                self.text(source.get(field), f"{identifier}.{field}")
            if kind == "example" and not self.example:
                self.error(identifier, "example sources are forbidden in real project records")
            if kind == "code":
                revision = self.code_ref(source.get("revision"), f"{identifier}.revision")
                path = self.text(source.get("path"), f"{identifier}.path")
                if path and (PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path):
                    self.error(identifier, "code path must be repository-relative without parent traversal")
                self.code_paths.append((revision, path, identifier))
        for identifier, decision in decisions.items():
            self.required(decision, ("answer",), identifier)
            self.text(decision.get("question"), f"{identifier}.question")
            state = self.choice(decision, "state", "open/confirmed/rejected", identifier)
            self.refs(decision, "sourceIds", sources, identifier, state in {"confirmed", "rejected"})
            if state == "open" and decision.get("answer") is not None:
                self.error(identifier, "open decision answer must be null")
            else:
                self.text(decision.get("answer"), f"{identifier}.answer", state == "open")
        for identifier, phase in phases.items():
            for field in ("title", "goal"):
                self.text(phase.get(field), f"{identifier}.{field}")
            self.strings(phase, "exitCriteria", identifier, True)
        features = {}
        for identifier, capability in capabilities.items():
            self.required(capability, ("phaseId",), identifier)
            layer = self.choice(capability, "layer", "PB/PD/PT", identifier)
            if layer and not identifier.startswith(layer):
                self.error(identifier, "capability ID must begin with its layer")
            for field in ("group", "title"):
                self.text(capability.get(field), f"{identifier}.{field}")
            self.refs(capability, "requires", capabilities, identifier)
            phase = self.text(capability.get("phaseId"), f"{identifier}.phaseId", True)
            if phase is not None and phase not in phases:
                self.error(identifier, f"unknown phase {phase!r}")
            if phase is None:
                self.warnings.append(f"{identifier}: no default phase assigned")
            assessment = self.assessment(capability, identifier, sources)
            if identifier in reviewed and (assessment.get("codeRef") != end or assessment.get("reviewedAt") != reviewed_date):
                self.error(identifier, "reviewed capability must match review.to and review.date")
            if end is None and assessment.get("implementation") != "not_assessed":
                self.error(identifier, "review.to=null permits only not_assessed capabilities")
            functions = self.array(capability.get("functions"), f"{identifier}.functions")
            if not functions and (phase is not None or assessment.get("implementation") != "not_assessed"):
                self.error(identifier, "empty capability must be unassessed and unassigned")
            for index, raw in enumerate(functions):
                loc = f"{identifier}.functions[{index}]"
                feature = self.obj(raw, loc)
                fid = self.text(feature.get("id"), f"{loc}.id")
                if fid in features:
                    self.error(loc, f"duplicate function ID {fid}")
                if not fid.startswith(identifier + ".") or not fid[len(identifier) + 1:]:
                    self.error(loc, "function ID must start with parent capability ID plus '.' and a nonempty suffix")
                self.text(feature.get("title"), f"{loc}.title")
                state = self.choice(feature, "requirementState", "confirmed/proposed/open/rejected/retired", loc)
                self.refs(feature, "sourceIds", sources, loc, state == "confirmed")
                self.refs(feature, "decisionIds", decisions, loc, state in {"proposed", "open"})
                self.strings(feature, "acceptance", loc, state == "confirmed")
                if fid:
                    features[fid] = feature
        targets = {}
        for identifier, release in releases.items():
            for field in ("title", "outcome"):
                self.text(release.get(field), f"{identifier}.{field}")
            phase = self.text(release.get("phaseId"), f"{identifier}.phaseId")
            if phase not in phases:
                self.error(identifier, f"unknown phase {phase!r}")
            state = self.choice(release, "state", "candidate/planned", identifier)
            self.refs(release, "dependsOn", releases, identifier)
            for field in ("entryCriteria", "acceptance", "outOfScope", "remainingWork", "measure", "rollout", "rollback"):
                self.strings(release, field, identifier, field in {"acceptance", "measure", "rollout", "rollback"})
            seen = set()
            for index, raw in enumerate(self.array(release.get("deliveries"), f"{identifier}.deliveries")):
                loc = f"{identifier}.deliveries[{index}]"
                delivery = self.obj(raw, loc)
                fid = self.text(delivery.get("featureId"), f"{loc}.featureId")
                delivery_mode = self.choice(delivery, "mode", "complete/slice/reuse", loc)
                self.text(delivery.get("scope"), f"{loc}.scope")
                self.strings(delivery, "acceptance", loc, True)
                key = (fid, delivery_mode)
                if key in seen:
                    self.error(loc, "duplicate delivery target and mode within release")
                seen.add(key)
                if fid not in features:
                    self.error(loc, f"unknown function {fid!r}")
                    continue
                requirement = features[fid].get("requirementState")
                if not isinstance(requirement, str):
                    requirement = ""
                if requirement in {"rejected", "retired"}:
                    self.error(loc, "rejected/retired function cannot remain in an active release")
                if state == "planned" and requirement != "confirmed":
                    self.error(loc, "planned release requires confirmed functions")
                decision_ids = features[fid].get("decisionIds", [])
                if state == "planned" and isinstance(decision_ids, list):
                    for decision_id in decision_ids:
                        if isinstance(decision_id, str) and decision_id in decisions and decisions[decision_id].get("state") == "open":
                            self.error(loc, f"planned function has unresolved decision {decision_id}")
                if delivery_mode == "complete":
                    targets[fid] = targets.get(fid, 0) + 1
        backlog = self.refs(self.data, "backlog", features, "document")
        for fid, feature in features.items():
            active = feature.get("requirementState") not in ("rejected", "retired")
            if active and targets.get(fid, 0) + (fid in backlog) != 1:
                self.error(fid, "active function needs exactly one complete target OR backlog entry")
            if not active and fid in backlog:
                self.error(fid, "rejected/retired function cannot remain in active backlog")
        if backlog:
            self.warnings.append(f"{len(backlog)} functions remain in backlog; not scheduled completion")
        self.check_cycle(capabilities, "requires", "capabilities")
        self.check_cycle(releases, "dependsOn", "releases")
        if self.repo and not self.example:
            self.verify_git()

    def verify_git(self):
        resolved = {}
        def run(arguments):
            try:
                return subprocess.run(["git", "-C", self.repo, *arguments], capture_output=True, text=True, timeout=15)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.error("--repo", f"cannot run read-only Git check: {exc}")
                return None
        repository = run(["rev-parse", "--git-dir"])
        if repository is None:
            return
        if repository.returncode:
            self.error("--repo", "not a readable local Git repository")
            return
        for revision in sorted(self.git_refs):
            result = run(["rev-parse", "--verify", "--end-of-options", revision + "^{commit}"])
            if result is not None and result.returncode == 0:
                resolved[revision] = result.stdout.strip()
            elif result is not None:
                self.error("--repo", f"commit does not resolve: {revision}")
        for revision, path, identifier in self.code_paths:
            if revision not in resolved or not path:
                continue
            result = run(["cat-file", "-e", resolved[revision] + ":" + path])
            if result is not None and result.returncode:
                self.error(identifier, f"code path does not exist at {revision}: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputfile", help="JSON capability map conforming to schemaVersion 1")
    parser.add_argument("--repo", help="local Git repository for optional read-only evidence checks")
    args = parser.parse_args()
    try:
        data = json.loads(Path(args.inputfile).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        print(f"ERROR: cannot read JSON: {exc}", file=sys.stderr)
        return 1
    validator = Validator(data, args.repo)
    validator.validate()
    for warning in validator.warnings:
        print(f"WARNING: {warning}")
    for error in validator.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if validator.errors:
        print(f"FAIL: {len(validator.errors)} error(s)", file=sys.stderr)
        return 1
    suffix = "; local Git evidence references checked" if args.repo else "; Git evidence existence not checked"
    print("PASS: capability map structural checks" + suffix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
