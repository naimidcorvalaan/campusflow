"""Small synthetic containers generated in memory; no private/binary fixtures."""
import io
import zipfile
from xml.sax.saxutils import escape

TASK_TEXT = 'Experiment one: complete steps 1 to 3 and write the report. Submit 2026-09-15 22:00.'
TASK_TWO = 'Experiment two: run the simulation and submit a short report. Submit 2026-09-22 22:00.'


def docx_bytes(text=TASK_TEXT, complex_content=False, extra=None):
    body = '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Course tasks</w:t></w:r></w:p>'
    body += '<w:p><w:r><w:t>{}</w:t></w:r></w:p>'.format(escape(text))
    if text:
        body += '<w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Check calculations</w:t></w:r></w:p>'
        body += '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Task</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Deadline</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    else:
        body = ''
    if complex_content:
        body += '<w:p><w:drawing/></w:p>'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        for name, value in (extra or {}).items():
            z.writestr(name, value)
    return stream.getvalue()


def assessment_docx_bytes(partial=False):
    """Synthetic blank form, with Word content controls and a nested table.

    Deliberately has no imperative '填写' and no course/task fixture filler.
    This is a structural regression sample, not the user's original document.
    """
    p = lambda text: '<w:p><w:r><w:t>{}</w:t></w:r></w:p>'.format(escape(text))
    cell = lambda content: '<w:tc>{}</w:tc>'.format(content)
    row = lambda label: '<w:tr>' + cell(p(label)) + cell(p('')) + '</w:tr>'
    scores = '<w:tbl>' + ''.join(row(label) for label in ('德', '智', '体', '美', '劳')) + '</w:tbl>'
    body = p('本科生综合素质测评考核表【主观评价部分】')
    body += '<w:sdt><w:sdtContent><w:tbl>' + row('姓名') + row('学号')
    body += '<w:tr>' + cell(p('各项评分')) + cell(scores) + '</w:tr>'
    body += row('自我评价（约300字，结合本学年经历）') + row('支撑材料名称')
    body += row('复核：信息完整、分数与证明一致') + row('辅导员签字')
    body += '</w:tbl></w:sdtContent></w:sdt>'
    if partial:
        body += '<w:p><w:r><w:drawing/></w:r></w:p>'
    return _docx_body_bytes(body)


def reference_docx_bytes():
    return _docx_body_bytes('<w:p><w:r><w:t>学校简介：校园始建于十九世纪，校训严谨治学。'
        '本文介绍校园历史与办学背景。</w:t></w:r></w:p>')


def _docx_body_bytes(body):
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>'
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx_bytes(''))) as template:
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', template.read('[Content_Types].xml'))
            archive.writestr('word/document.xml', xml)
    return stream.getvalue()


def pdf_bytes(pages=(TASK_TEXT, TASK_TWO)):
    """Minimal PDF writer: string=text page, None=scan image, ''=blank page."""
    from PIL import Image, ImageDraw
    objects = [b'', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for text in pages:
        if text is None:
            image = Image.new('RGB', (1000, 1400), 'white')
            ImageDraw.Draw(image).text((50, 100), 'Experiment report: steps 1-3. Show results.', fill='black')
            data = io.BytesIO()
            image.save(data, format='JPEG')
            raw = data.getvalue()
            objects.append(b'<< /Type /XObject /Subtype /Image /Width 1000 /Height 1400 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ' + str(len(raw)).encode() + b' >>\nstream\n' + raw + b'\nendstream')
            image_id = len(objects)
            content = b'q 595 0 0 842 0 0 cm /Im0 Do Q'
            resources = '/XObject << /Im0 {} 0 R >>'.format(image_id)
        else:
            # Wrap fixture lines to remain visible in rendered inspection.
            lines = [text[i:i+65] for i in range(0, len(text), 65)]
            escaped = lambda s: s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            content = ('BT /F1 12 Tf 40 790 Td 18 TL ' +
                       ' '.join('({}) Tj T*'.format(escaped(s)) for s in lines) + ' ET').encode('ascii')
            resources = '/Font << /F1 3 0 R >>'
        objects.append(b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream')
        content_id = len(objects)
        objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << ' + resources + ' >> /Contents {} 0 R >>'.format(content_id)).encode())
        kids.append(len(objects))
    objects[0] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objects[1] = ('<< /Type /Pages /Kids [' + ' '.join('{} 0 R'.format(i) for i in kids) + '] /Count {} >>'.format(len(kids))).encode()
    result = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend('{} 0 obj\n'.format(number).encode() + obj + b'\nendobj\n')
    xref = len(result)
    result.extend('xref\n0 {}\n0000000000 65535 f \n'.format(len(offsets)).encode())
    for offset in offsets[1:]:
        result.extend('{:010d} 00000 n \n'.format(offset).encode())
    result.extend('trailer << /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF'.format(len(offsets), xref).encode())
    return bytes(result)
