"""Explicit non-task reference zones, retaining the complete source text.

Only a declared background/reference block with an explicit exclusion starts
a zone. The word 'background' alone never removes an assignment. This is a
structural boundary shared by scope and source provenance, not text rewriting.
"""
import re

_START=re.compile(r'(?m)^[ \t#]*(?:以下|下面)(?:为|是)[^\n。；]{0,55}(?:背景|参考|附录)')
_EXCLUSION=re.compile(r'仅供(?:背景|参考)|不构成[^。；]{0,35}(?:任务|要求)|不要求[^。；]{0,20}(?:执行|完成)')
_RESUME=re.compile(r'(?m)^[ \t#]*(?:(?:以下|下面)(?:为|是)(?:本次)?(?:作业|任务)要求|(?:作业|任务)要求\s*[：:]|(?:背景|参考)(?:资料|说明)?结束)')


def reference_regions(text):
    regions=[]
    for match in _START.finditer(text):
        if any(start<=match.start()<end for start,end in regions):continue
        following=_RESUME.search(text,match.end())
        end=following.start() if following else len(text)
        # PDF line wrapping is permitted inside the declaration, but a distant
        # negative sentence cannot turn an ordinary heading into an exclusion.
        declaration=text[match.start():min(end,match.start()+240)]
        if _EXCLUSION.search(declaration):regions.append((match.start(),end))
    return tuple(regions)
