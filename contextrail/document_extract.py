"""Bounded text and source coordinates for explicitly selected local documents."""

from collections import Counter
from io import BytesIO
from pathlib import PurePosixPath
import warnings
from zipfile import ZipFile

FORMATS = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}
INPUT_BYTES = 24 * 1024 * 1024
MAX_PAGES = 300
MAX_UNITS = 1200
MAX_TEXT_BYTES = 384 * 1024
MAX_UNIT_BYTES = 8192
MAX_XML_BYTES = 96 * 1024 * 1024
MAX_SHEETS = 32
MAX_ROWS = 2000
MAX_COLUMNS = 128


class Extraction:
    def __init__(self, format):
        self.format = format
        self.units = []
        self.counts = Counter()
        self.omissions = []
        self.remaining = MAX_TEXT_BYTES
        self.limited = False

    def omit(self, code, locator=None, count=1):
        self.counts[code] += count
        if len(self.omissions) < 24:
            self.omissions.append({"reason": code, "locator": locator, "count": count})

    def add(self, locator, text):
        if not text or not text.strip():
            return
        encoded = text.encode("utf-8")
        if len(self.units) >= MAX_UNITS or not self.remaining:
            self.omit("document_budget_reached", locator)
            self.limited = True
            return
        allowance = min(MAX_UNIT_BYTES, self.remaining)
        if len(encoded) > allowance:
            self.omit("text_truncated", locator)
            text = encoded[:allowance].decode("utf-8", "ignore")
            if allowance == self.remaining:
                self.limited = True
                self.omit("document_budget_reached", locator)
        self.remaining -= len(text.encode("utf-8"))
        if text:
            self.units.append({"locator": locator, "text": text})

    def result(self):
        return {"format": self.format, "units": self.units,
                "loss": {"complete": not self.counts, "counts": dict(self.counts),
                         "omissions": self.omissions, "boundary": "embedded_text_and_supported_cells"}}


def _office_members(raw):
    with ZipFile(BytesIO(raw)) as archive:
        members = archive.infolist()
        if (len(members) > 4096 or sum(member.file_size for member in members) > MAX_XML_BYTES
                or any(member.flag_bits & 1 for member in members)):
            raise ValueError("Office package expansion or encryption exceeds the import boundary.")
        return {member.filename for member in members}


def _pdf(raw, result):
    from pypdf import PdfReader

    document = PdfReader(BytesIO(raw))
    if document.is_encrypted:
        result.omit("encrypted_document")
        return
    pages = len(document.pages)
    if pages > MAX_PAGES:
        result.omit("page_limit", count=pages - MAX_PAGES)
    for index in range(min(pages, MAX_PAGES)):
        locator = {"kind": "pdf_page", "page": index + 1}
        try:
            text = document.pages[index].extract_text() or ""
        except Exception:
            result.omit("page_parse_failed", locator)
            continue
        if text.strip():
            result.add(locator, text)
        else:
            result.omit("no_extractable_text", locator)
        if result.limited:
            break


def _word(raw, result, names):
    from docx import Document
    from docx.table import Table

    if any(name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments"))
           and name.endswith(".xml") for name in names):
        result.omit("word_outside_main_body")
    doc = Document(BytesIO(raw))
    paragraph = 0
    table = 0
    for block in doc.iter_inner_content():
        if result.limited:
            break
        if not isinstance(block, Table):
            paragraph += 1
            result.add({"kind": "word_paragraph", "paragraph": paragraph}, block.text)
            continue
        table += 1
        merged = set()
        for row_number, row in enumerate(block.rows, 1):
            for column, cell in enumerate(row.cells, row.grid_cols_before + 1):
                locator = {"kind": "word_cell", "table": table, "row": row_number, "column": column}
                if cell._tc in merged:
                    continue
                merged.add(cell._tc)
                if cell.tables:
                    result.omit("nested_word_table_not_extracted", locator, len(cell.tables))
                result.add(locator, cell.text)
                if result.limited:
                    break
            if result.limited:
                break


def _excel(raw, result, names):
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    if any(name.startswith(("xl/comments", "xl/drawings", "xl/charts")) for name in names):
        result.omit("excel_comments_or_drawings_not_extracted")
    formulas = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    try:
        cached = load_workbook(BytesIO(raw), read_only=True, data_only=True, keep_links=False)
        try:
            if len(formulas.worksheets) > MAX_SHEETS:
                result.omit("worksheet_limit", count=len(formulas.worksheets) - MAX_SHEETS)
            for ws in formulas.worksheets[:MAX_SHEETS]:
                _excel_sheet(ws, cached[ws.title], result, get_column_letter)
                if result.limited:
                    break
        finally:
            cached.close()
    finally:
        formulas.close()


def _excel_sheet(ws, cached, result, column_name):
    if ((ws.max_row is None or ws.max_row > MAX_ROWS)
            or (ws.max_column is None or ws.max_column > MAX_COLUMNS)):
        result.omit("worksheet_grid_limit", {"kind": "worksheet", "sheet": ws.title})
    options = {"max_row": min(ws.max_row or MAX_ROWS, MAX_ROWS),
               "max_col": min(ws.max_column or MAX_COLUMNS, MAX_COLUMNS)}
    for row_number, (formulas, values) in enumerate(zip(ws.iter_rows(**options), cached.iter_rows(**options)), 1):
        for column, (cell, value) in enumerate(zip(formulas, values), 1):
            if cell.value is None:
                continue
            locator = {"kind": "excel_cell", "sheet": ws.title, "cell": column_name(column) + str(row_number)}
            if cell.data_type == "f":
                text = cell.value if isinstance(cell.value, str) else getattr(cell.value, "text", None)
                if value.value is None:
                    result.omit("formula_cached_value_missing", locator)
                else:
                    text = f"{text or 'Formula'}\nCached value: {value.value}"
                if not text:
                    result.omit("formula_text_unavailable", locator)
            else:
                text = str(cell.value)
            result.add(locator, text)
            if result.limited:
                return


def _slides(raw, result):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    deck = Presentation(BytesIO(raw))
    if len(deck.slides) > MAX_PAGES:
        result.omit("slide_limit", count=len(deck.slides) - MAX_PAGES)

    def read_shapes(shapes, slide, parent=()):
        for index, shape in enumerate(shapes, 1):
            if result.limited:
                return
            position = (*parent, index)
            coordinates = {"slide": slide, "shape": index}
            if parent:
                coordinates["shape_path"] = list(position)
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                read_shapes(shape.shapes, slide, position)
            elif shape.has_table:
                for row_number, row in enumerate(shape.table.rows, 1):
                    for column, cell in enumerate(row.cells, 1):
                        if not cell.is_spanned:
                            result.add({"kind": "slide_cell", **coordinates,
                                        "row": row_number, "column": column}, cell.text)
                        if result.limited:
                            return
            elif shape.has_text_frame:
                result.add({"kind": "slide_text", **coordinates}, shape.text)
            else:
                result.omit("nontext_slide_shape", coordinates)

    for index, slide in enumerate(deck.slides, 1):
        if index > MAX_PAGES or result.limited:
            break
        if slide.has_notes_slide:
            result.omit("slide_notes_not_extracted", {"kind": "slide", "slide": index})
        read_shapes(slide.shapes, index)


def extract(raw, suffix):
    format = FORMATS[suffix]
    result = Extraction(format)
    try:
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always")
            if format == "pdf":
                _pdf(raw, result)
            else:
                names = _office_members(raw)
                if any(PurePosixPath(name).parts[:2] in {("word", "media"), ("xl", "media"), ("ppt", "media")}
                       for name in names):
                    result.omit("embedded_media_not_extracted")
                if format == "docx":
                    _word(raw, result, names)
                elif format == "xlsx":
                    _excel(raw, result, names)
                else:
                    _slides(raw, result)
            if notices:
                result.omit("parser_warning", count=len(notices))
    except ImportError:
        result.omit("optional_parser_unavailable")
    except Exception:
        # Third-party parser details can include private filenames or content.
        result.omit("document_parse_failed")
    return result.result()
