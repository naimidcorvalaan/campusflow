"""Meaningful planning variants through the production handler, temporary state.

Reuses the audited smoke harness's official guards. No expected result is fed
into the model. Categories not checked by this harness are not claimed tested.
"""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.reliability_live import isolated_environment,write
from scripts.fc_fixtures import ENDPOINT


def harness():
    from scripts import reliability_planning_core as module
    return module


def cases(variant_count=5):
    base=harness().scenarios();result=[]
    subjects=[('高数','物理','英语','概率论','算法'),('线代','化学','德语','统计学','编程'),
              ('离散数学','生物','法语','数值分析','数据库'),('微积分','力学','日语','随机过程','网络'),
              ('优化方法','电路','俄语','运筹学','操作系统')]
    if not 1<=variant_count<=15:raise ValueError('At most 15 distinct workload/campus variants')
    for variant in range(variant_count):
        subjects_for_day=subjects[variant%len(subjects)]
        for item in base:
            case=deepcopy(item);case['scenario_id']='planning-{:03d}'.format(len(result)+1)
            for old,new in zip(('高数','物理','英语','概率论','算法'),subjects_for_day):
                case['input_summary']=case['input_summary'].replace(old,new)
            # Vary actual task duration while retaining the independent hard
            # windows and feasibility expectations, not simply repeated text.
            minutes=case['expected_minutes']
            replacements={m:m+variant*2 for m in minutes}
            import re
            case['input_summary']=re.sub(r'(\d+)分钟',lambda m:str(replacements.get(int(m[1]),int(m[1])))+'分钟',case['input_summary'])
            case['expected_minutes']=sorted(replacements[m] for m in minutes)
            if case['category']=='fixed_course':
                from src.p3_class_prep import CLASS_ARRIVAL_LEAD_MINUTES
                now=re.search(r'现在(\d+):(\d+)',case['input_summary'])
                start=re.search(r'(\d+):(\d+)到',case['input_summary'])
                capacity=(int(start[1])*60+int(start[2])-int(now[1])*60-int(now[2])-CLASS_ARRIVAL_LEAD_MINUTES)
                case['expected_preclass_capacity']=capacity
                case['expected_complete']=sum(case['expected_minutes'])<=capacity
            if variant%2:
                if case['campus']=='beiyangyuan':
                    case['campus']='weijinlu';case['input_summary']=case['input_summary'].replace('北洋园','卫津路').replace('第31教学楼','第9教学楼')
                    # Only verified campus venue names; meal cases keep original
                    # campus below, avoiding invented restaurant mappings.
                else:
                    case['campus']='beiyangyuan';case['input_summary']=case['input_summary'].replace('卫津路','北洋园').replace('第9教学楼','第31教学楼')
                if case['category']=='dinner_window':
                    case['campus']=item['campus'];case['input_summary']=case['input_summary'].replace('卫津路','北洋园')
            result.append(case)
    assert len(result)==variant_count*10 and len({c['input_summary'] for c in result})==variant_count*10
    return result


def run(selected,destination):
    import requests
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    destination.mkdir(parents=True,exist_ok=False);write(destination/'gold.json',selected)
    module=harness();results=[]
    with isolated_environment():
        adapter=TJUP2CallAdapter();original=requests.sessions.Session.request
        module.OUT=destination
        meter=module.Meter(ENDPOINT,original)
        requests.sessions.Session.request=lambda session,method,url,**kw:meter.request(session,method,url,**kw)
        try:
            for case in selected:
                result=module.run_case(case,meter,adapter);results.append(result)
                write(destination/'results.json',results);write(destination/'requests.json',meter.records)
                print(json.dumps(dict(case=case['scenario_id'],pass_=result['success'],failures=result['failures'],
                    requests=result['actual_model_requests'],infrastructure=meter.circuit)),flush=True)
                if meter.circuit:break
        finally:requests.sessions.Session.request=original
    summary=dict(planned=len(selected),executed=len(results),passed=sum(r['success'] for r in results),
        requests=len(meter.records),usage={k:sum((r['usage'] or {}).get(k,0) for r in meter.records)
        for k in ('prompt_tokens','completion_tokens','total_tokens')},
        maximum_input=max(((r['usage'] or {}).get('prompt_tokens',0) for r in meter.records),default=0),
        infrastructure=meter.circuit,official_data_unchanged=True,
        scope='initial planning: fixed windows, routes, order, meal, shortage, duration provenance; no dynamic turn claims')
    write(destination/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--start',type=int,default=0);p.add_argument('--limit',type=int,default=10)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--run',action='store_true');p.add_argument('--variants',type=int,default=5);a=p.parse_args()
    selected=cases(a.variants)[a.start:a.start+a.limit]
    if a.run:run(selected,a.output)
    else:print(json.dumps(selected,ensure_ascii=False,indent=2))
