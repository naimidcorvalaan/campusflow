"""Layout equivalence and exact source slices; never rewrite source content."""
import re

_CJK_FOLD=re.compile(r'(?<=[\u3400-\u9fff，。；：、！？（）《》“”])[ \t]*\r?\n[ \t]*(?=[\u3400-\u9fff，。；：、！？（）《》“”])')


def normalized_workload_quote(text):
    # A single physical wrap inside Chinese text introduces no word separator.
    # Keep blank paragraph boundaries, English spaces, punctuation and order.
    return re.sub(r'\s+',' ',_CJK_FOLD.sub('',text)).strip()


def workload_quote_matches(quote,source):
    # Preserve the pre-existing equivalence of line-separated fields and
    # space-separated quotations, in addition to Chinese physical wraps.
    plain=lambda text:re.sub(r'\s+',' ',text).strip()
    return (plain(quote) in plain(source)
        or normalized_workload_quote(quote) in normalized_workload_quote(source))


def _layout_with_offsets(text,fold_cjk):
    ignored=set()
    if fold_cjk:
        for match in _CJK_FOLD.finditer(text):ignored.update(range(*match.span()))
    chars=[];offsets=[]
    for index,char in enumerate(text):
        if index in ignored:continue
        char=' ' if char.isspace() else char
        if char==' ' and (not chars or chars[-1]==' '):continue
        chars.append(char);offsets.append(index)
    if chars and chars[-1]==' ':chars.pop();offsets.pop()
    return ''.join(chars),offsets


def canonical_source_quote(quote,source,limit=600):
    """Recover one exact source slice under established layout equivalence.

    No semantic rewriting or skipped intervening text. Multiple possible source
    spans remain unresolved. The caller still applies its verbatim guard.
    """
    if not quote or len(quote)>limit:return None
    if quote in source:return quote
    candidates=set()
    for fold in (False,True):
        needle,_=_layout_with_offsets(quote,fold)
        haystack,offsets=_layout_with_offsets(source,fold)
        if not needle:continue
        start=haystack.find(needle)
        while start>=0:
            candidates.add((offsets[start],offsets[start+len(needle)-1]+1))
            if len(candidates)>1:return None
            start=haystack.find(needle,start+1)
    if len(candidates)!=1:return None
    start,end=next(iter(candidates));original=source[start:end]
    return original if len(original)<=limit else None
