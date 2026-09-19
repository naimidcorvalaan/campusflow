"""Six requested failures first; the original ten are gated on six passes."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.fc_original_ten import main

DEST=ROOT/'artifacts/evaluation/tju-20260916/function-semantic-business'
FAILED=(1,3,4,6,7,8)


def run(stage):
    if stage=='six':main(FAILED,DEST/'six')
    elif stage=='ten':
        rows=json.loads((DEST/'six/calls.json').read_text(encoding='utf-8'))
        if [r['fixture'] for r in rows]!=list(FAILED) or not all(r['final_pass'] for r in rows):
            raise RuntimeError('Six-fixture gate did not pass; full ten prohibited')
        main(tuple(range(1,11)),DEST/'ten')
    else:raise ValueError('Choose six or ten')


if __name__=='__main__':run(sys.argv[1])
