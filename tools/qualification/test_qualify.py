from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qualify import grade_case, load_json, validate_suite


ROOT = Path(__file__).resolve().parents[2]
PYTHON_TEST_COMMAND = [
    "{python}",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-p",
    "test_*.py",
    "-q",
]


def case_definition() -> dict:
    return {
        "id": "mini-negativtest",
        "title": "Interne Sabotageprobe",
        "max_seconds": 5,
        "allowed_paths": ["src/guard.py", "tests/test_guard.py"],
        "required_submission_paths": ["src/guard.py", "tests/test_guard.py"],
        "candidate_test_command": PYTHON_TEST_COMMAND,
        "starter_files": {
            "src/guard.py": (
                "def positive(value):\n"
                "    raise NotImplementedError\n"
            )
        },
    }


def hidden_definition() -> dict:
    return {
        "forbidden_patterns": ["requests.", "socket."],
        "critical_test_quality": True,
        "mutations": [
            {
                "name": "Negativprüfung entfernt",
                "path": "src/guard.py",
                "content": (
                    "def positive(value):\n"
                    "    return value\n"
                ),
            }
        ],
        "hidden_files": {
            ".qualification_hidden/test_guard.py": (
                "import sys, unittest\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
                "from guard import positive\n"
                "class GuardTest(unittest.TestCase):\n"
                "    def test_contract(self):\n"
                "        self.assertEqual(positive(2), 2)\n"
                "        with self.assertRaises(ValueError):\n"
                "            positive(0)\n"
                "if __name__ == '__main__': unittest.main()\n"
            )
        },
        "correctness_command": ["{python}", ".qualification_hidden/test_guard.py"],
        "safety_files": {},
        "documentation_checks": [],
    }


def write_submission(root: Path, *, negative_test: bool, extra_path: bool) -> None:
    source = root / "mini-negativtest"
    (source / "src").mkdir(parents=True)
    (source / "tests").mkdir(parents=True)
    (source / "src/guard.py").write_text(
        "def positive(value):\n"
        "    if value < 1:\n"
        "        raise ValueError('muss positiv sein')\n"
        "    return value\n",
        encoding="utf-8",
    )
    test = (
        "import sys, unittest\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from guard import positive\n"
        "class CandidateTest(unittest.TestCase):\n"
        "    def test_happy_path(self): self.assertEqual(positive(2), 2)\n"
    )
    if negative_test:
        test += (
            "    def test_negative(self):\n"
            "        with self.assertRaises(ValueError): positive(0)\n"
        )
    test += "if __name__ == '__main__': unittest.main()\n"
    (source / "tests/test_guard.py").write_text(test, encoding="utf-8")
    if extra_path:
        (source / "README.md").write_text(
            "Happy-Path-Tests bleiben grün.\n", encoding="utf-8"
        )


class QualificationToolTest(unittest.TestCase):
    def test_eingefrorene_suite_ist_gueltig(self):
        suite = load_json(ROOT / "tasks/qualification/suite.json")
        hidden = load_json(ROOT / "tasks/qualification/hidden-tests.json")
        validate_suite(suite, hidden)
        self.assertEqual(len(suite["cases"]), 10)

    def test_aufgabendateien_haben_gueltige_syntax(self):
        suite = load_json(ROOT / "tasks/qualification/suite.json")
        hidden = load_json(ROOT / "tasks/qualification/hidden-tests.json")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for case in suite["cases"]:
                files = {
                    **case["starter_files"],
                    **hidden["cases"][case["id"]].get("hidden_files", {}),
                    **hidden["cases"][case["id"]].get("safety_files", {}),
                }
                for relative, content in files.items():
                    path = base / case["id"] / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                    if path.suffix == ".py":
                        compile(content, str(path), "exec")
                    elif path.suffix in {".js", ".mjs", ".cjs"}:
                        subprocess.run(
                            ["node", "--check", str(path)],
                            check=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )

    def test_scope_sabotage_faellt_trotz_gruener_tests_durch(self):
        with tempfile.TemporaryDirectory() as tmp:
            submissions = Path(tmp)
            write_submission(submissions, negative_test=True, extra_path=True)
            result = grade_case(
                case_definition(),
                hidden_definition(),
                submissions,
            )
        self.assertTrue(result["candidate_tests"]["ok"])
        self.assertTrue(result["correctness"])
        self.assertFalse(result["scope"])
        self.assertTrue(
            any("Scope-Verstoß" in item for item in result["critical_violations"])
        )

    def test_entfernter_negativtest_faellt_trotz_happy_path_durch(self):
        with tempfile.TemporaryDirectory() as tmp:
            submissions = Path(tmp)
            write_submission(submissions, negative_test=False, extra_path=False)
            result = grade_case(
                case_definition(),
                hidden_definition(),
                submissions,
            )
        self.assertTrue(result["candidate_tests"]["ok"])
        self.assertTrue(result["correctness"])
        self.assertFalse(result["test_quality"])
        self.assertTrue(
            any("Negativtest" in item for item in result["critical_violations"])
        )


if __name__ == "__main__":
    unittest.main()
