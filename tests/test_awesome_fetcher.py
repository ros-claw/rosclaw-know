"""Tests for Phase 8 awesome-list fetcher (parsing only — no network)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ROSCLAW_KNOW_MOCK_LLM", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rosclaw_know.awesome_fetcher import (  # noqa: E402
    AwesomeEntry,
    _classify,
    _slug,
    _write_entry_md,
    parse_readme,
)


class ParseReadmeTest(unittest.TestCase):
    def test_simple_bullets_with_sections(self) -> None:
        md = (
            "# Awesome Control Theory\n\n"
            "## Books\n"
            "- [Control Engineering](https://example.com/book) - The classic intro text.\n"
            "- [Robust Control](https://example.com/robust) - Modern coverage.\n\n"
            "## Software\n"
            "* [pycontrol](https://github.com/owner/pycontrol) - Python toolbox.\n"
        )
        entries = parse_readme(md)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].title, "Control Engineering")
        self.assertEqual(entries[0].url, "https://example.com/book")
        self.assertEqual(entries[0].section, "Books")
        self.assertEqual(entries[2].section, "Software")
        self.assertEqual(entries[2].url, "https://github.com/owner/pycontrol")

    def test_dash_variants_in_description(self) -> None:
        md = (
            "## X\n"
            "- [A](https://a.com) – em-dash.\n"
            "- [B](https://b.com) — long-dash.\n"
            "- [C](https://c.com) : colon-sep.\n"
            "- [D](https://d.com) regular.\n"  # no separator at all
        )
        entries = parse_readme(md)
        descs = [e.description for e in entries]
        self.assertIn("em-dash.", descs)
        self.assertIn("long-dash.", descs)
        self.assertIn("colon-sep.", descs)
        # "regular" line still parses URL+title, description may be empty or
        # whatever — just confirm we got 4 entries
        self.assertEqual(len(entries), 4)

    def test_top_level_bullets_get_top_section(self) -> None:
        md = "- [Top thing](https://top.com)\n## Lower\n- [Other](https://other.com)\n"
        entries = parse_readme(md)
        self.assertEqual(entries[0].section, "(top)")
        self.assertEqual(entries[1].section, "Lower")

    def test_non_bullet_lines_ignored(self) -> None:
        md = (
            "## Stuff\n"
            "Lorem ipsum.\n"
            "- [Real entry](https://r.com) - desc\n"
            "Random other line\n"
        )
        entries = parse_readme(md)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Real entry")


class ClassifyTest(unittest.TestCase):
    def test_github_repo(self) -> None:
        self.assertEqual(_classify("https://github.com/owner/repo"), "github_repo")

    def test_github_with_subpath_still_github(self) -> None:
        self.assertEqual(_classify("https://github.com/owner/repo/blob/main/README"), "github_repo")

    def test_pdf(self) -> None:
        self.assertEqual(_classify("https://arxiv.org/pdf/1234.5678.pdf"), "pdf")

    def test_html(self) -> None:
        self.assertEqual(_classify("https://example.com/blog/post"), "html")

    def test_github_pdf_release_treated_as_pdf(self) -> None:
        # A direct PDF link inside github (e.g. raw release) → pdf wins
        self.assertEqual(_classify("https://github.com/owner/repo/raw/main/paper.pdf"), "pdf")


class SlugTest(unittest.TestCase):
    def test_strip_punctuation(self) -> None:
        self.assertEqual(_slug("Hello, World!"), "hello_world")

    def test_caps_to_lower(self) -> None:
        self.assertEqual(_slug("ABCdef"), "abcdef")

    def test_length_capped(self) -> None:
        out = _slug("x" * 200, max_chars=20)
        self.assertEqual(len(out), 20)

    def test_empty_input_fallback(self) -> None:
        self.assertEqual(_slug("!@#$%"), "untitled")


class WriteEntryMdTest(unittest.TestCase):
    def test_writes_frontmatter_and_body(self) -> None:
        entry = AwesomeEntry(
            title="My Lib",
            url="https://github.com/me/mylib",
            description="A tiny utility.",
            section="Tools",
        )
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            path = _write_entry_md(entry, "github_readme", "## body content\nMore.\n", "ctrl_theory", tdp)
            text = path.read_text(encoding="utf-8")
        self.assertIn("source: https://github.com/me/mylib", text)
        self.assertIn("title: My Lib", text)
        self.assertIn("section: Tools", text)
        self.assertIn("priority: 0", text)
        self.assertIn("## body content", text)
        # Description is rendered as italic blurb under the h1
        self.assertIn("_A tiny utility._", text)


if __name__ == "__main__":
    unittest.main()
