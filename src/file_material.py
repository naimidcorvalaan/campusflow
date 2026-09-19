"""Bounded, local-only document source adapters. No storage, network or model calls.

All payload fields are ephemeral and excluded from repr. Only source metadata
is copied to MaterialDraft. PDFium calls are serialized across Streamlit threads.
"""
import hashlib
import io
import math
import re
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from defusedxml import ElementTree as SafeXML

MAX_DOCX_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_VISION_PAGES = 5
# Product admission limit, not a claim about the provider context window.
# Capacity research found larger successful files, but repeated tool-adherence
# failures do not yet establish a stable interval for a larger default.
# Isolated capacity evaluations may patch this one constant for their process;
# there is deliberately no environment/UI switch changing the default.
MAX_TEXT_CHARS = 12000
MAX_RENDER_EDGE = 1800
RENDER_DPI = 140
MAX_VISION_BYTES = 5 * 1024 * 1024  # total before base64, not per page
MAX_ZIP_MEMBERS = 1000
MAX_ZIP_EXPANDED = 32 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_ZIP_RATIO = 200
MIN_PAGE_TEXT_CHARS = 40
MIN_IMAGE_PAGE_COVERAGE = .10
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
PDF_MIME = 'application/pdf'
FILE_SOURCE_TYPES = ('docx', 'pdf_text', 'pdf_vision')
_PDF_LOCK = threading.RLock()  # synchronization only; never contains user data
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class FileMaterialError(ValueError):
    """Messages are deliberately constant, with no parser exception/file content."""


@dataclass(frozen=True)
class FileMaterial:
    source_type: str
    name: str
    mime: str
    size: int
    digest: str
    text: str = field(repr=False)
    page_count: int = 0
    selected_pages: tuple = ()
    vision_pages: tuple = ()
    images: tuple = field(default=(), repr=False)
    warnings: tuple = ()

    @property
    def fingerprint(self):
        return hashlib.sha256((self.digest + ':' + ','.join(map(str, self.selected_pages))).encode()).hexdigest()


def parse_page_range(value, page_count):
    """One-based, bounded, sorted page selection; duplicates are input mistakes."""
    if not value.strip():
        return tuple(range(1, page_count + 1))
    if len(value) > 150:
        raise FileMaterialError('页码过长，请使用 1-3, 7 这样的格式。')
    pages = set()
    for part in value.replace('，', ',').split(','):
        match = re.fullmatch(r'\s*([0-9]{1,3})(?:\s*-\s*([0-9]{1,3}))?\s*', part)
        if not match:
            raise FileMaterialError('页码格式不正确，请使用 1-3, 7 这样的格式。')
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= page_count:
            raise FileMaterialError('页码越界或范围倒置，请检查这份PDF的实际页数。')
        selected = set(range(first, last + 1))
        if pages.intersection(selected):
            raise FileMaterialError('页码重复，请让每页只出现一次。')
        pages.update(selected)
    return tuple(sorted(pages))


def _text_limit(text):
    if len(text) > MAX_TEXT_CHARS:
        raise FileMaterialError('可读取文字超过{}字，请缩小Word材料，或选择较少的PDF页码。未截断分析。'.format(MAX_TEXT_CHARS))
    return text


def read_file_material(name, mime, data, page_range='', render=False):
    suffix = PurePosixPath(str(name).replace('\\', '/')).suffix.lower()
    if suffix == '.doc':
        raise FileMaterialError('暂不支持 .doc，请另存为 .docx 后再试。')
    if suffix not in ('.docx', '.pdf'):
        raise FileMaterialError('请选择 DOCX 或 PDF 文件。')
    expected = DOCX_MIME if suffix == '.docx' else PDF_MIME
    if mime != expected:
        raise FileMaterialError('文件类型与扩展名不一致，请重新选择文件。')
    limit = MAX_DOCX_BYTES if suffix == '.docx' else MAX_PDF_BYTES
    if not isinstance(data, bytes) or not data or len(data) > limit:
        raise FileMaterialError('文件为空或超过10MB，请缩小后重试。')
    if suffix == '.docx' and not data.startswith(b'PK\x03\x04'):
        raise FileMaterialError('这不是有效的DOCX文件，请检查文件内容。')
    if suffix == '.pdf' and not data.startswith(b'%PDF-'):
        raise FileMaterialError('这不是有效的PDF文件，请检查文件内容。')
    digest = hashlib.sha256(data).hexdigest()
    # Filenames are display-only; never used as a filesystem path or prompt.
    name = PurePosixPath(str(name).replace('\\', '/')).name[:200]
    try:
        if suffix == '.docx':
            text, warnings = _read_docx(data)
            return FileMaterial('docx', name, expected, len(data), digest, text, warnings=warnings)
        with _PDF_LOCK:
            return _read_pdf(name, data, digest, page_range, render)
    except FileMaterialError:
        raise
    except Exception:
        raise FileMaterialError('文件无法读取，可能已损坏、加密或格式不受支持；请重新导出后再试。') from None


def _read_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_MEMBERS or sum(e.file_size for e in entries) > MAX_ZIP_EXPANDED:
            raise FileMaterialError('Word内部内容过大，无法安全读取，请简化文档后再试。')
        names = [e.filename for e in entries]
        if len(set(names)) != len(names):
            raise FileMaterialError('Word容器结构异常，请重新导出。')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                    or ':' in entry.filename or entry.flag_bits & 1
                    or entry.file_size > max(1, entry.compress_size) * MAX_ZIP_RATIO):
                raise FileMaterialError('Word容器不符合安全读取限制，请重新导出。')
        if not {'[Content_Types].xml', 'word/document.xml'}.issubset(names):
            raise FileMaterialError('DOCX缺少正文结构，请重新导出。')

        def xml(name):
            if archive.getinfo(name).file_size > MAX_XML_BYTES:
                raise FileMaterialError('Word正文结构过大，请缩小材料。')
            return SafeXML.fromstring(archive.read(name), forbid_dtd=True)

        content_types = xml('[Content_Types].xml')
        if not any(e.get('PartName') == '/word/document.xml' and e.get('ContentType') ==
                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
                   for e in content_types):
            raise FileMaterialError('仅支持普通DOCX文档，不支持宏或其他Office容器。')
        root = xml('word/document.xml')
        body = root.find(_W + 'body')
        if body is None:
            raise FileMaterialError('Word中没有可读取正文。')

        def paragraph(node):
            parts = []
            for e in node.iter():
                if e.tag == _W + 't':
                    parts.append(e.text or '')
                elif e.tag in (_W + 'tab', _W + 'br', _W + 'cr'):
                    parts.append(' ')
            value = ''.join(parts).strip()
            style = node.find('./' + _W + 'pPr/' + _W + 'pStyle')
            if style is not None and re.match(r'(Heading|Title)', style.get(_W + 'val', ''), re.I):
                value = '# ' + value
            elif (node.find('./' + _W + 'pPr/' + _W + 'numPr') is not None
                  or (style is not None and style.get(_W + 'val', '').lower().startswith('list'))):
                value = '• ' + value
            return value

        def content_children(node):
            # Word wraps whole tables, rows, cells or paragraphs in content
            # controls. Unwrap their content, never their properties/metadata.
            for child in node:
                if child.tag == _W + 'sdt':
                    content = child.find(_W + 'sdtContent')
                    if content is not None:
                        yield from content_children(content)
                elif child.tag == _W + 'customXml':
                    yield from content_children(child)
                else:
                    yield child

        def blocks(node):
            for block in content_children(node):
                if block.tag == _W + 'p':
                    yield paragraph(block)
                elif block.tag == _W + 'tbl':
                    yield '表格：'
                    for row in content_children(block):
                        if row.tag == _W + 'tr':
                            yield ' | '.join(' / '.join(value for value in blocks(cell) if value.strip())
                                or '（空白）' for cell in content_children(row) if cell.tag == _W + 'tc')

        lines = list(blocks(body))
        complex_tags = {'drawing', 'pict', 'object', 'txbxContent', 'oMath', 'altChunk'}
        complex_content = (any(e.tag.rsplit('}', 1)[-1] in complex_tags for e in root.iter())
                           or any(n.startswith(('word/media/', 'word/embeddings/', 'word/header',
                                                'word/footer', 'word/comments', 'word/footnotes')) for n in names))
        text = _text_limit('\n'.join(x for x in lines if x.strip()).strip())
        if not text or not any(c.isalnum() for c in text):
            raise FileMaterialError('这份Word主要由图片或复杂内容组成，当前版本暂不能完整读取其中内容。'
                                    if complex_content else '这份Word没有可读取的任务文字。')
        warnings = ('这份Word里还有图片或复杂内容，本次主要按可读取文字估算。',) if complex_content else ()
        return text, warnings


def _read_pdf(name, data, digest, page_range, render):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)  # no form environment, JS, XFA or external links
    try:
        count = len(doc)
        if not 1 <= count <= MAX_PDF_PAGES:
            raise FileMaterialError('PDF需包含1–20页，请先拆分较长的文件。')
        pages = parse_page_range(page_range, count)
        chunks, vision, images = [], [], []
        for number in pages:
            page = doc[number - 1]
            try:
                width, height = page.get_size()
                if not all(math.isfinite(v) and 0 < v <= 14400 for v in (width, height)):
                    raise FileMaterialError('PDF页面尺寸异常，无法安全读取。')
                tp = page.get_textpage()
                try:
                    if tp.count_chars() > MAX_TEXT_CHARS:
                        raise FileMaterialError('单页文字过长，请缩小材料；未截断分析。')
                    text = tp.get_text_bounded().replace('\r\n', '\n').strip()
                finally:
                    tp.close()
                effective = sum(c.isalnum() for c in text)
                bad = text.count('\ufffd') / max(1, len(text))
                # Per-page checks avoid losing scanned pages in a mostly text PDF.
                # Sparse/page-number-only and image-bearing pages use vision.
                objects = tuple(page.get_objects())
                image_area = 0
                for obj in objects:
                    if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                        left, bottom, right, top = obj.get_bounds()
                        image_area += max(0,min(width,right)-max(0,left)) * max(0,min(height,top)-max(0,bottom))
                # A tiny decorative logo alone does not turn a text page into vision.
                has_image = image_area / (width * height) >= MIN_IMAGE_PAGE_COVERAGE
                text_reliable = (effective >= MIN_PAGE_TEXT_CHARS and bad < .05
                                 and effective / max(1,len(text)) >= .3 and len(set(text)) >= 8)
                if text:
                    chunks.append('[第{}页]\n{}'.format(number, text))
                if has_image or (not text_reliable and (objects or text)):
                    vision.append(number)
                if len(vision) > MAX_VISION_PAGES:
                    raise FileMaterialError('这份PDF需要查看的图文页较多，请选择要分析的页码（一次最多5个图文页）。')
                if render and number in vision:
                    scale = min(RENDER_DPI / 72, MAX_RENDER_EDGE / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        pil = bitmap.to_pil()
                        try:
                            buffer = io.BytesIO()
                            pil.save(buffer, format='PNG')
                            image = buffer.getvalue()
                        finally:
                            pil.close()
                    finally:
                        bitmap.close()
                    images.append(('image/png', image))
                    if sum(len(b) for _, b in images) > MAX_VISION_BYTES:
                        raise FileMaterialError('所选页面图片总量超过5MB，请减少页码后再试。')
            finally:
                page.close()
        text = _text_limit('\n\n'.join(chunks))
        if not text and not vision:
            raise FileMaterialError('这份PDF没有可读取的文字或页面内容。')
        return FileMaterial('pdf_vision' if vision else 'pdf_text', name, PDF_MIME, len(data),
                            digest, text, count, pages, tuple(vision), tuple(images))
    finally:
        doc.close()
