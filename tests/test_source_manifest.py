"""Tests for ``rosclaw_know.source_manifest``."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("ROSCLAW_KNOW_MOCK_LLM", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.source_manifest import (  # noqa: E402
    SCHEMA_VERSION,
    SourceManifest,
    SourceRecord,
    sha256_of,
)


class Sha256Test(unittest.TestCase):
    def test_known_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "hello.txt"
            fp.write_bytes(b"hello\n")
            # echo -n "hello\n" | sha256sum
            self.assertEqual(
                sha256_of(fp),
                "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
            )


class ManifestRoundTripTest(unittest.TestCase):
    def test_empty_load_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            m = SourceManifest.load(path=Path(td) / "not-there.json")
            self.assertEqual(m.files, {})

    def test_status_classification(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            mpath = tdp / "manifest.json"
            paper = tdp / "paper.md"
            paper.write_text("# initial content\n", encoding="utf-8")

            m = SourceManifest.load(path=mpath)
            self.assertEqual(m.status_of(paper), "new")

            m.upsert(paper)
            m.save()
            m2 = SourceManifest.load(path=mpath)
            self.assertEqual(m2.status_of(paper), "unchanged")

            paper.write_text("# initial content\n\n## new section\n", encoding="utf-8")
            self.assertEqual(m2.status_of(paper), "changed")

    def test_select_dirty_filters_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            a = tdp / "a.md"
            b = tdp / "b.md"
            c = tdp / "c.md"
            for f, txt in ((a, "alpha"), (b, "beta"), (c, "gamma")):
                f.write_text(txt, encoding="utf-8")

            m = SourceManifest.load(path=tdp / "m.json")
            # First time: all three are NEW
            dirty = m.select_dirty([a, b, c])
            self.assertEqual({p.name for p, _ in dirty}, {"a.md", "b.md", "c.md"})
            for p, _status in dirty:
                m.upsert(p)

            # Touch only b's content, leave a and c alone
            b.write_text("beta v2", encoding="utf-8")
            dirty = m.select_dirty([a, b, c])
            self.assertEqual({(p.name, s) for p, s in dirty}, {("b.md", "changed")})

    def test_first_processed_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            f = tdp / "doc.md"
            f.write_text("v1", encoding="utf-8")
            m = SourceManifest.load(path=tdp / "m.json")
            first_record = m.upsert(f)
            v1_hash = first_record.sha256

            f.write_text("v2", encoding="utf-8")
            second_record = m.upsert(f)

            self.assertEqual(second_record.first_processed, first_record.first_processed)
            self.assertNotEqual(second_record.sha256, v1_hash)
            self.assertGreaterEqual(second_record.last_processed, first_record.first_processed)

    def test_record_contribution_accumulates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            f = tdp / "doc.md"
            f.write_text("body", encoding="utf-8")
            m = SourceManifest.load(path=tdp / "m.json")
            m.upsert(f, n_clusters_contributed=2)
            m.record_contribution(f, n_extra_clusters=3)
            self.assertEqual(m.files[str(f.resolve())].n_clusters_contributed, 5)

    def test_save_then_load_preserves_records(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            f = tdp / "doc.md"
            f.write_text("body", encoding="utf-8")
            mpath = tdp / "m.json"
            m = SourceManifest.load(path=mpath)
            m.upsert(f, domain="Control_Locomotion", n_clusters_contributed=1)
            m.save()

            payload = json.loads(mpath.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], SCHEMA_VERSION)

            reloaded = SourceManifest.load(path=mpath)
            ap = str(f.resolve())
            self.assertIn(ap, reloaded.files)
            self.assertIsInstance(reloaded.files[ap], SourceRecord)
            self.assertEqual(reloaded.files[ap].domain, "Control_Locomotion")
            self.assertEqual(reloaded.files[ap].n_clusters_contributed, 1)

    def test_remove(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            f = tdp / "doc.md"
            f.write_text("body", encoding="utf-8")
            m = SourceManifest.load(path=tdp / "m.json")
            m.upsert(f)
            self.assertTrue(m.remove(f))
            self.assertFalse(m.remove(f))  # second remove is a no-op

    def test_corrupt_manifest_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            mpath = tdp / "m.json"
            mpath.write_text("{ not valid json", encoding="utf-8")
            m = SourceManifest.load(path=mpath)
            self.assertEqual(m.files, {})


if __name__ == "__main__":
    unittest.main()
