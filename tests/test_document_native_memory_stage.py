"""G08 consumer contracts over the original queue and federated query."""
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from contextrail import Store, Target
from contextrail.capture_policy import SCHEMA as CAPTURE_SCHEMA
from contextrail.host import ContextTools
from contextrail.project import load_project
from contextrail.source_events import SCHEMA as EVENT_SCHEMA


class DocumentNativeMemoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / "工程"
        self.workspace.mkdir()
        self.home = self.root / "memory"
        self.home.mkdir()
        self.store_path = self.root / "rail.sqlite"
        self.config = load_project(self.workspace, overrides={"task": "documents", "store": str(self.store_path)})
        self.store = Store(self.store_path)
        self.addCleanup(self.store.close)
        self.store.create_task(self.config.scope, Target("owner", "host", "local"),
                               "Review explicit reference documents.", allowed_providers=("host",))
        self.db = sqlite3.connect(self.home / "state.sqlite")
        self.addCleanup(self.db.close)
        self.db.execute("""CREATE TABLE jobs(id TEXT PRIMARY KEY, source TEXT, payload TEXT,
            state TEXT, created REAL, available REAL)""")
        self.db.executescript(EVENT_SCHEMA + CAPTURE_SCHEMA)
        self.native = self.root / "claude" / "MEMORY.md"
        self.native.parent.mkdir()
        self.binding = {
            "schema": 1, "workspace": str(self.workspace), "task": "documents", "memory_home": str(self.home),
            "document_imports": True,
            "native_memory_files": [{"id": "selected", "client": "claude", "path": str(self.native)}],
        }

    def cli_import(self, path):
        run = subprocess.run(
            [sys.executable, "-m", "contextrail", "document-import", "--workspace", str(self.workspace),
             "--task", "documents", "--store", str(self.store_path), "--session", "owner", "--epoch", "0",
             "--memory-home", str(self.home), "--file", str(path)], capture_output=True, text=True,
            encoding="utf-8", timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def tools(self, binding=None):
        return ContextTools(self.store, self.config.scope, "owner", query_binding=binding or self.binding)

    def hit(self, phrase, source, *, binding=None):
        result = self.tools(binding).call("context.query", {"queries": [phrase], "budget": 24000})
        row = result["queries"][0]
        return next(hit for hit in row["hits"] if hit["origin"] == source)

    def reopen(self, ref, *, binding=None):
        return self.tools(binding).call("context.material_get", {"ref": ref})

    def pdf(self, path, phrase):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        doc = PdfWriter()
        page = doc.add_blank_page(width=300, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): doc._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 10 50 Td ({phrase}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = doc._add_object(content)
        doc.add_blank_page(width=300, height=200)
        with path.open("wb") as stream:
            doc.write(stream)

    def test_pdf_page_and_blank_loss_reopen_exact_archived_revision(self):
        path = self.workspace / "资料.pdf"
        self.pdf(path, "QuartzManualOriginal")
        first = self.cli_import(path)
        self.assertEqual(first["status"], "queued_not_yet_classified")
        self.assertEqual(first["loss"]["counts"]["no_extractable_text"], 1)
        self.assertEqual(first["queued"], 1)
        repeated = self.cli_import(path)
        self.assertEqual(repeated["document_id"], first["document_id"])
        self.assertEqual(repeated["queued"], 0)
        hit = self.hit("QuartzManualOriginal", "documents")
        self.assertEqual(hit["locator"], {"kind": "pdf_page", "page": 1})
        self.pdf(path, "QuartzManualUpdated")
        new = self.cli_import(path)
        self.assertNotEqual(new["document_id"], first["document_id"])
        self.assertIn("QuartzManualOriginal", self.reopen(hit["ref"])["content"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_office_anchors_preserve_word_excel_and_powerpoint_structure(self):
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation
        from pptx.util import Inches
        path = self.workspace / "reference.docx"
        word = Document()
        word.add_paragraph("WordParagraphEvidence")
        table = word.add_table(rows=1, cols=2)
        table.cell(0, 1).text = "WordCellEvidence"
        word.save(path)
        self.cli_import(path)
        sheet_path = self.workspace / "reference.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "数据"
        sheet["B4"] = "SpreadsheetEvidence"
        sheet["C4"] = "=1+2"
        book.save(sheet_path)
        excel = self.cli_import(sheet_path)
        self.assertGreaterEqual(excel["loss"]["counts"]["formula_cached_value_missing"], 1)
        slides_path = self.workspace / "reference.pptx"
        slides = Presentation()
        slide = slides.slides.add_slide(slides.slide_layouts[6])
        table = slide.shapes.add_table(1, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
        table.cell(0, 1).text = "SlideCellEvidence"
        slides.save(slides_path)
        self.cli_import(slides_path)
        for phrase, location in (
            ("WordParagraphEvidence", {"kind": "word_paragraph", "paragraph": 1}),
            ("WordCellEvidence", {"kind": "word_cell", "table": 1, "row": 1, "column": 2}),
            ("SpreadsheetEvidence", {"kind": "excel_cell", "sheet": "数据", "cell": "B4"}),
            ("SlideCellEvidence", {"kind": "slide_cell", "slide": 1, "shape": 1, "row": 1, "column": 2}),
        ):
            hit = self.hit(phrase, "documents")
            self.assertEqual(hit["locator"], location)
            self.assertIn(phrase, self.reopen(hit["ref"])["content"])

    def test_native_memory_search_is_read_only_bound_and_exact(self):
        self.native.write_text("# Heading\n\nSelectedNativeReference\n", encoding="utf-8")
        hidden = self.root / "MEMORY.md"
        hidden.write_text("HiddenNativeReference\n", encoding="utf-8")
        hit = self.hit("SelectedNativeReference", "native_memory")
        self.assertEqual(hit["evidence_role"], "client_auto_memory")
        self.assertEqual(hit["locator"]["start_line"], 3)
        self.assertEqual(self.reopen(hit["ref"])["content"], "SelectedNativeReference")
        path = self.root / "sources.json"
        path.write_text(json.dumps(self.binding), encoding="utf-8")
        run = subprocess.run([sys.executable, "-m", "contextrail", "material-get",
                              "--workspace", str(self.workspace), "--task", "documents",
                              "--store", str(self.store_path), "--session", "owner",
                              "--query-binding", str(path), "--ref", json.dumps(hit["ref"])],
                             capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["content"], "SelectedNativeReference")
        result = self.tools().call("context.query", {"queries": ["HiddenNativeReference"], "budget": 24000})
        self.assertFalse(any(row["origin"] == "native_memory" for row in result["queries"][0]["hits"]))
        self.native.write_text("# Heading\n\nChangedNativeReference\n", encoding="utf-8")
        from contextrail.errors import StaleSnapshot
        with self.assertRaises(StaleSnapshot):
            self.reopen(hit["ref"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_capture_exclusion_and_archive_reopen_are_workspace_bound(self):
        from contextrail.errors import NotFound

        (self.workspace / ".contextrail.toml").write_text('[capture]\nignore = ["private.pdf"]\n',
                                                         encoding="utf-8")
        excluded = self.workspace / "private.pdf"
        excluded.write_bytes(b"must not reach the parser")
        report = self.cli_import(excluded)
        self.assertEqual(report["status"], "policy_excluded")
        self.assertIsNone(report["document_id"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

        accepted = self.workspace / "reference.pdf"
        self.pdf(accepted, "WorkspacePrivateArchive")
        self.cli_import(accepted)
        ref = self.hit("WorkspacePrivateArchive", "documents")["ref"]
        second = self.root / "second"
        second.mkdir()
        config = load_project(second, overrides={"task": "other", "store": str(self.store_path)})
        self.store.create_task(config.scope, Target("reader", "host", "local"), "Read another project.",
                               allowed_providers=("host",))
        binding = {**self.binding, "workspace": str(second), "task": "other", "native_memory_files": []}
        tools = ContextTools(self.store, config.scope, "reader", query_binding=binding)
        result = tools.call("context.query", {"queries": ["WorkspacePrivateArchive"], "budget": 24000})
        self.assertFalse(any(row["origin"] == "documents" for row in result["queries"][0]["hits"]))
        with self.assertRaises(NotFound):
            tools.call("context.material_get", {"ref": ref})
