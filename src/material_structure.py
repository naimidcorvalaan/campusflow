"""Source layout helpers; page locators are metadata, not task names."""
import re

_PAGE_LINE = re.compile(r'^\s*(?:\[\s*第\s*\d+\s*页\s*\]|page\s+\d+(?:\s+of\s+\d+)?)\s*$', re.I)
_PAGE_TAG = re.compile(r'\[\s*第\s*\d+\s*页\s*\]', re.I)


def content_lines(text):
    # Only reserved page locators; ordinary bracketed headings remain intact.
    return [line for line in text.splitlines() if not _PAGE_LINE.fullmatch(line)]


def content_text(text):
    return '\n'.join(content_lines(text))


def display_name(value, source=''):
    cleaned = _PAGE_TAG.sub('', content_text(value)).strip()
    if cleaned:
        return cleaned
    return source_title(source) if source else '完成材料任务'


def source_title(text):
    lines = [line.lstrip('# ').strip() for line in content_lines(text) if line.strip()]
    if not lines:
        return '完成材料任务'
    title = lines[0]
    # A page break can separate a name from a conventional document suffix.
    # Never join arbitrary body paragraphs into a title.
    if len(lines)>1 and re.fullmatch(r'(?:实验)?(?:指导书|任务书|作业说明|申请表|评审说明)', lines[1]):
        title += lines[1]
    return title[:90]
