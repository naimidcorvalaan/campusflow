from scripts.reliability_round_report import usage_summary


def test_missing_usage_is_not_invented_or_extrapolated():
    value=usage_summary([
        {'http_status':200,'usage':{'prompt_tokens':10,'completion_tokens':4,'total_tokens':14}},
        {'status_code':400,'usage':None},
        {'http_status':200,'input_tokens':20,'output_tokens':5,'total_tokens':25},
    ])
    assert value['requests']==3 and value['usage_known']==2
    assert value['known_total_tokens']==39 and value['maximum_input']==20
    assert value['http_statuses']=={'200':2,'400':1}


def test_repair_counts_use_all_recorders_without_double_counting():
    value=usage_summary([{'stage':'repair'},
                        {'request_stage':'recognition_repair','repair':True},
                        {'stage':'initial'}])
    assert value['repair_requests']==2


def test_scope_uses_planned_turns_not_successful_turn_count():
    from scripts.reliability_round_report import trajectory_scope
    assert trajectory_scope([0,0])=='initial_planning'
    assert trajectory_scope([2,2])=='feedback_prefix'
    assert trajectory_scope([5,5])=='continuous_trajectory'
    assert trajectory_scope([None])=='trajectory_scope_unknown'
