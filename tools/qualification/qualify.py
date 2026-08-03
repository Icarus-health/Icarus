#!/usr/bin/env python3
"""Deterministischer Offline-Grader für Icarus-Ausführungsmodelle."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class QualificationError(RuntimeError):
    """Die Suite oder eine Einreichung verletzt den Qualifikationsvertrag."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise QualificationError(f"{path} muss ein JSON-Objekt enthalten.")
    return value


def safe_relative(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise QualificationError(f"Unsicherer relativer Pfad: {path_value!r}")
    return path


def validate_suite(suite: dict[str, Any], hidden: dict[str, Any]) -> None:
    errors: list[str] = []
    weights = suite.get("weights")
    expected_weights = {
        "correctness": 50,
        "test_quality": 20,
        "scope": 15,
        "safety": 10,
        "documentation": 5,
    }
    if weights != expected_weights:
        errors.append(f"Gewichte müssen exakt {expected_weights} sein.")

    if suite.get("schema_version") != 1 or hidden.get("schema_version") != 1:
        errors.append("Suite und versteckte Tests müssen schema_version 1 verwenden.")
    if suite.get("suite_id") != hidden.get("suite_id"):
        errors.append("Suite-ID und versteckte Tests passen nicht zusammen.")
    if suite.get("frozen") is not True:
        errors.append("Die Suite muss als eingefroren markiert sein.")

    cases = suite.get("cases")
    hidden_cases = hidden.get("cases")
    if not isinstance(cases, list) or len(cases) != 10:
        errors.append("Die Suite muss genau zehn Aufgaben enthalten.")
        cases = []
    if not isinstance(hidden_cases, dict):
        errors.append("hidden-tests.json braucht ein cases-Objekt.")
        hidden_cases = {}

    required_coverage = {
        "python",
        "javascript",
        "persistence",
        "api",
        "ui",
        "scope",
        "negative-test",
        "documentation",
    }
    coverage: set[str] = set()
    ids: set[str] = set()

    for case in cases:
        if not isinstance(case, dict):
            errors.append("Jede Aufgabe muss ein Objekt sein.")
            continue
        case_id = str(case.get("id") or "")
        if not case_id or case_id in ids:
            errors.append(f"Ungültige oder doppelte Aufgaben-ID: {case_id!r}")
            continue
        ids.add(case_id)
        coverage.update(str(item) for item in case.get("coverage", []))
        if case_id not in hidden_cases:
            errors.append(f"Versteckte Tests fehlen für {case_id}.")
        timeout = case.get("max_seconds")
        if not isinstance(timeout, int) or not 1 <= timeout <= 30:
            errors.append(f"{case_id}: max_seconds muss zwischen 1 und 30 liegen.")
        for key in ("allowed_paths", "required_submission_paths"):
            values = case.get(key)
            if not isinstance(values, list):
                errors.append(f"{case_id}: {key} muss eine Liste sein.")
                continue
            for value in values:
                try:
                    safe_relative(str(value))
                except QualificationError as exc:
                    errors.append(f"{case_id}: {exc}")
        files = case.get("starter_files")
        if not isinstance(files, dict):
            errors.append(f"{case_id}: starter_files muss ein Objekt sein.")
        else:
            for path_value, content in files.items():
                try:
                    safe_relative(str(path_value))
                except QualificationError as exc:
                    errors.append(f"{case_id}: {exc}")
                if not isinstance(content, str):
                    errors.append(f"{case_id}: Starterdateien müssen Text enthalten.")
        command = case.get("candidate_test_command")
        if not isinstance(command, list) or not command:
            errors.append(f"{case_id}: candidate_test_command fehlt.")

        hidden_case = hidden_cases.get(case_id, {})
        if isinstance(hidden_case, dict):
            for group in ("hidden_files", "safety_files"):
                values = hidden_case.get(group, {})
                if not isinstance(values, dict):
                    errors.append(f"{case_id}: {group} muss ein Objekt sein.")
                    continue
                for path_value, content in values.items():
                    try:
                        safe_relative(str(path_value))
                    except QualificationError as exc:
                        errors.append(f"{case_id}: {exc}")
                    if not isinstance(content, str):
                        errors.append(f"{case_id}: {group} muss Text enthalten.")
            mutations = hidden_case.get("mutations")
            if not isinstance(mutations, list) or not mutations:
                errors.append(f"{case_id}: mindestens eine Mutation ist erforderlich.")
            else:
                allowed_paths = {
                    str(item) for item in case.get("allowed_paths", [])
                }
                for mutation in mutations:
                    if not isinstance(mutation, dict):
                        errors.append(f"{case_id}: Mutation muss ein Objekt sein.")
                        continue
                    try:
                        mutation_path = safe_relative(
                            str(mutation.get("path") or "")
                        )
                        if mutation_path.as_posix() not in allowed_paths:
                            errors.append(
                                f"{case_id}: Mutation liegt außerhalb der erlaubten Pfade."
                            )
                    except QualificationError as exc:
                        errors.append(f"{case_id}: {exc}")
                    if not isinstance(mutation.get("content"), str):
                        errors.append(f"{case_id}: Mutation braucht vollständigen Text.")

    missing_coverage = required_coverage - coverage
    if missing_coverage:
        errors.append(
            "Abdeckung fehlt: " + ", ".join(sorted(missing_coverage))
        )
    if set(hidden_cases) != ids:
        errors.append("Sichtbare und versteckte Aufgaben-IDs müssen identisch sein.")

    pass_rules = suite.get("pass_rules")
    if not isinstance(pass_rules, dict) or not isinstance(pass_rules.get("roles"), dict):
        errors.append("pass_rules.roles fehlt.")
    elif set(pass_rules["roles"]) != {"B", "C"}:
        errors.append("Nur die Rollenklassen B und C dürfen ausgewiesen werden.")

    if errors:
        raise QualificationError("\n".join(f"- {item}" for item in errors))


def _write_files(root: Path, files: dict[str, str]) -> None:
    for path_value, content in files.items():
        path = root / safe_relative(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _command(command: Iterable[str]) -> list[str]:
    return [sys.executable if item == "{python}" else item for item in command]


def run_command(command: list[str], cwd: Path, timeout: float) -> dict[str, Any]:
    home = cwd / ".qualification_home"
    temp = cwd / ".qualification_tmp"
    home.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    # Kandidatencode erbt absichtlich nicht die Umgebung des Entwicklers. Nur
    # Laufzeitpfade und neutrale Prozesswerte werden weitergegeben; Schlüssel,
    # Konten und lokale Icarus-Konfiguration bleiben außerhalb des Laufs.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "ICARUS_QUALIFICATION_OFFLINE": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMPDIR": str(temp),
        "TEMP": str(temp),
        "TMP": str(temp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
    }
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            _command(command),
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "runtime_seconds": round(time.monotonic() - started, 3),
            "output": completed.stdout[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = ""
        if exc.stdout:
            output = (
                exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else exc.stdout
            )
        return {
            "ok": False,
            "returncode": None,
            "runtime_seconds": round(time.monotonic() - started, 3),
            "output": (output + "\nZeitlimit überschritten.")[-4000:],
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "runtime_seconds": round(time.monotonic() - started, 3),
            "output": str(exc),
        }


def _submission_files(submission: Path) -> list[tuple[str, Path]]:
    if not submission.is_dir():
        return []
    result: list[tuple[str, Path]] = []
    for path in sorted(submission.rglob("*")):
        if path.is_symlink():
            rel = path.relative_to(submission).as_posix()
            raise QualificationError(f"Symlink in Einreichung: {rel}")
        if path.is_file():
            rel = path.relative_to(submission).as_posix()
            safe_relative(rel)
            result.append((rel, path))
    return result


def _documentation_ok(workspace: Path, checks: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for check in checks:
        path_value = str(check["path"])
        path = workspace / safe_relative(path_value)
        if not path.is_file():
            failures.append(f"{path_value} fehlt.")
            continue
        text = path.read_text(encoding="utf-8")
        for expected in check.get("contains", []):
            if str(expected) not in text:
                failures.append(f"{path_value} enthält {expected!r} nicht.")
        for forbidden in check.get("excludes", []):
            if str(forbidden) in text:
                failures.append(f"{path_value} enthält verbotenes {forbidden!r}.")
    return not failures, failures


def _safety_scan(
    submission_files: list[tuple[str, Path]],
    patterns: list[str],
) -> list[str]:
    violations: list[str] = []
    for rel, path in submission_files:
        if path.suffix.lower() not in {".py", ".js", ".mjs", ".cjs", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if pattern in text:
                violations.append(f"{rel}: verbotenes Muster {pattern!r}")
    return violations


def _apply_mutation(workspace: Path, mutation: dict[str, Any]) -> None:
    path = workspace / safe_relative(str(mutation["path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(mutation["content"]), encoding="utf-8")


def grade_case(
    case: dict[str, Any],
    hidden_case: dict[str, Any],
    submission_root: Path,
) -> dict[str, Any]:
    case_id = str(case["id"])
    timeout = float(case["max_seconds"])
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        return max(0.01, deadline - time.monotonic())

    allowed = {str(item) for item in case["allowed_paths"]}
    required = {str(item) for item in case["required_submission_paths"]}
    case_submission = submission_root / case_id
    critical: list[str] = []

    with tempfile.TemporaryDirectory(prefix=f"icarus-qual-{case_id}-") as tmp:
        workspace = Path(tmp)
        _write_files(workspace, case["starter_files"])

        try:
            submitted = _submission_files(case_submission)
        except QualificationError as exc:
            submitted = []
            critical.append(str(exc))

        submitted_paths = {rel for rel, _ in submitted}
        extras = sorted(submitted_paths - allowed)
        missing = sorted(required - submitted_paths)
        if extras:
            critical.append("Scope-Verstoß: " + ", ".join(extras))
        scope_ok = not extras and not missing

        for rel, source in submitted:
            if rel in allowed:
                target = workspace / safe_relative(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

        safety_patterns = [
            str(item) for item in hidden_case.get("forbidden_patterns", [])
        ]
        safety_violations = _safety_scan(submitted, safety_patterns)
        if safety_violations:
            critical.extend("Sicherheitsverstoß: " + item for item in safety_violations)

        candidate_tests = run_command(
            list(case["candidate_test_command"]), workspace, remaining()
        )
        mutation_results: list[dict[str, Any]] = []
        test_quality_ok = candidate_tests["ok"]
        if test_quality_ok:
            for mutation in hidden_case["mutations"]:
                with tempfile.TemporaryDirectory(
                    prefix=f"icarus-mutant-{case_id}-"
                ) as mutant_tmp:
                    mutant = Path(mutant_tmp)
                    shutil.copytree(workspace, mutant, dirs_exist_ok=True)
                    _apply_mutation(mutant, mutation)
                    result = run_command(
                        list(case["candidate_test_command"]), mutant, remaining()
                    )
                    killed = not result["ok"]
                    mutation_results.append(
                        {
                            "name": str(mutation.get("name") or mutation["path"]),
                            "killed": killed,
                            "runtime_seconds": result["runtime_seconds"],
                            "output": result["output"],
                        }
                    )
                    if not killed:
                        test_quality_ok = False
        if hidden_case.get("critical_test_quality") and not test_quality_ok:
            critical.append(
                "Kritischer Negativtest fehlt oder erkennt die definierte Mutation nicht."
            )

        _write_files(workspace, hidden_case.get("hidden_files", {}))
        correctness = run_command(
            list(hidden_case["correctness_command"]), workspace, remaining()
        )

        _write_files(workspace, hidden_case.get("safety_files", {}))
        safety_command = hidden_case.get("safety_command")
        safety_result = (
            run_command(list(safety_command), workspace, remaining())
            if safety_command
            else {"ok": True, "runtime_seconds": 0.0, "output": ""}
        )
        safety_ok = not safety_violations and safety_result["ok"]
        if not safety_ok:
            critical.append("Kritischer Sicherheitstest fehlgeschlagen.")

        doc_checks = list(hidden_case.get("documentation_checks", []))
        documentation_ok, documentation_failures = _documentation_ok(
            workspace, doc_checks
        )

        return {
            "id": case_id,
            "title": case["title"],
            "correctness": bool(correctness["ok"]),
            "test_quality": bool(test_quality_ok),
            "scope": bool(scope_ok),
            "safety": bool(safety_ok),
            "documentation": bool(documentation_ok) if doc_checks else None,
            "critical_violations": critical,
            "submitted_paths": sorted(submitted_paths),
            "missing_required_paths": missing,
            "extra_paths": extras,
            "candidate_tests": candidate_tests,
            "mutations": mutation_results,
            "correctness_test": correctness,
            "safety_test": safety_result,
            "documentation_failures": documentation_failures,
            "runtime_seconds": round(timeout - remaining() + 0.01, 3),
            "max_seconds": timeout,
        }


def _category_score(
    case_results: list[dict[str, Any]],
    field: str,
    weight: int,
    *,
    nullable: bool = False,
) -> float:
    values = [item[field] for item in case_results]
    if nullable:
        values = [value for value in values if value is not None]
    if not values:
        return float(weight)
    return round(weight * sum(bool(value) for value in values) / len(values), 2)


def _role_for(
    scores: dict[str, float],
    critical: list[str],
    rules: dict[str, Any],
) -> str:
    if critical:
        return "nicht_qualifiziert"
    total = sum(scores.values())
    for role in ("B", "C"):
        rule = rules["roles"][role]
        if (
            total >= float(rule["minimum_total"])
            and scores["correctness"] >= float(rule["minimum_correctness"])
            and scores["test_quality"] >= float(rule["minimum_test_quality"])
        ):
            return role
    return "nicht_qualifiziert"


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unbekannt"


def grade(
    suite: dict[str, Any],
    hidden: dict[str, Any],
    submissions: Path,
    *,
    commit: str | None,
    cost_eur: float,
    run_id: str | None,
) -> dict[str, Any]:
    validate_suite(suite, hidden)
    started = time.monotonic()
    case_results = [
        grade_case(case, hidden["cases"][case["id"]], submissions)
        for case in suite["cases"]
    ]
    weights = suite["weights"]
    scores = {
        "correctness": _category_score(
            case_results, "correctness", weights["correctness"]
        ),
        "test_quality": _category_score(
            case_results, "test_quality", weights["test_quality"]
        ),
        "scope": _category_score(case_results, "scope", weights["scope"]),
        "safety": _category_score(case_results, "safety", weights["safety"]),
        "documentation": _category_score(
            case_results,
            "documentation",
            weights["documentation"],
            nullable=True,
        ),
    }
    critical = [
        f"{item['id']}: {violation}"
        for item in case_results
        for violation in item["critical_violations"]
    ]
    role = _role_for(scores, critical, suite["pass_rules"])
    return {
        "schema_version": 1,
        "suite_id": suite["suite_id"],
        "suite_version": suite["suite_version"],
        "run_id": run_id or f"run-{uuid.uuid4().hex[:12]}",
        "commit": commit or _git_commit(),
        "date_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "role_class": role,
        "passed": role != "nicht_qualifiziert",
        "runtime_seconds": round(time.monotonic() - started, 3),
        "cost_eur": round(cost_eur, 4),
        "score_total": round(sum(scores.values()), 2),
        "scores": scores,
        "critical_violations": critical,
        "cases": case_results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Icarus-Eignungstest für Ausführungsmodelle"
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("tasks/qualification/suite.json"),
    )
    parser.add_argument(
        "--hidden",
        type=Path,
        default=Path("tasks/qualification/hidden-tests.json"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="eingefrorene Suite prüfen")

    grade_parser = subparsers.add_parser("grade", help="Einreichungen bewerten")
    grade_parser.add_argument("--submissions", type=Path, required=True)
    grade_parser.add_argument("--report", type=Path)
    grade_parser.add_argument("--commit")
    grade_parser.add_argument("--run-id")
    grade_parser.add_argument("--cost-eur", type=float, default=0.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        suite = load_json(args.suite)
        hidden = load_json(args.hidden)
        if args.command == "validate":
            validate_suite(suite, hidden)
            print(
                f"{suite['suite_id']} ist gültig: "
                f"{len(suite['cases'])} eingefrorene Aufgaben."
            )
            return 0

        if args.cost_eur < 0:
            raise QualificationError("Kosten dürfen nicht negativ sein.")
        report = grade(
            suite,
            hidden,
            args.submissions,
            commit=args.commit,
            cost_eur=args.cost_eur,
            run_id=args.run_id,
        )
        output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
            print(f"Bericht: {args.report}")
        print(
            f"Rollenklasse: {report['role_class']} · "
            f"{report['score_total']:.2f}/100 · "
            f"{report['runtime_seconds']:.3f}s · "
            f"{report['cost_eur']:.4f} EUR"
        )
        return 0 if report["passed"] else 1
    except (QualificationError, OSError, json.JSONDecodeError) as exc:
        print(f"Qualifikation fehlgeschlagen: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
