"""Repo cleanup regressions: dead modules stay deleted, historical docs live in docs/archive/, links resolve."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "docs" / "archive"
DELETED = ("PROGRESO.md", "runtime/provider.py", "runtime/certify_finnhub.py", "runtime/certify_official_macro.py")
ROOT_DOCS = {"AGENTS.md", "CLAUDE.md", "CHANGELOG_AGENT.md"}
MAINTAINED = ("AGENTS.md", "CLAUDE.md", "docs/STATUS.md", "docs/WORKFLOW.md", "docs/decisions.md", "docs/issues.md",
              "pyproject.toml", "runtime/system_health.py", "test_phase7_ai_characterization.py")
MD_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
ARCHIVE_REF = re.compile(r"(?:docs/)?archive/([A-Za-z0-9_]+\.md)")


class RepoCleanupTests(unittest.TestCase):
    def test_dead_files_stay_deleted(self):
        for rel in DELETED:
            self.assertFalse((ROOT / rel).exists(), rel)

    def test_only_entry_docs_remain_at_root(self):
        self.assertEqual({p.name for p in ROOT.glob("*.md") if not p.name.startswith(".")}, ROOT_DOCS)

    def test_historical_docs_are_archived(self):
        for name in ("EXPERIMENT_FREEZE.md", "V1_POSTMORTEM.md", "V2_HANDOFF_REQUIREMENTS.md",
                     "V2_PHASE8_P86_DESIGN.md", "AUDIT_FREEZE.md", "HIGH81_OWNER_DECISION.md"):
            self.assertTrue((ARCHIVE / name).is_file(), name)

    def test_relative_markdown_links_resolve(self):
        docs = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
        for doc in docs:
            for target in MD_LINK.findall(doc.read_text(encoding="utf-8")):
                if "://" in target or target.startswith("mailto:"):
                    continue
                with self.subTest(doc=str(doc.relative_to(ROOT)), target=target):
                    self.assertTrue((doc.parent / target).exists())

    def test_archive_references_in_maintained_files_resolve(self):
        for rel in MAINTAINED + tuple(str(p.relative_to(ROOT)) for p in (ROOT / "docs" / "phases").glob("*.md")):
            for name in ARCHIVE_REF.findall((ROOT / rel).read_text(encoding="utf-8")):
                with self.subTest(file=rel, name=name):
                    self.assertTrue((ARCHIVE / name).is_file())


if __name__ == "__main__":
    unittest.main()
