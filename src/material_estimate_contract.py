"""One final estimate contract for normal, repaired and recovered candidates.

The extraction envelope remains strict. Recovery may remove known context
echoes and supply null optional metadata, then must pass that same parser.
"""
import json
from src.material_formal_validation import canonical_envelope,feedback
from src.material_estimate_diagnostics import record,record_formal_failure
from src.material_quantity_semantics import requirement_value,semantic_value,MATERIAL_OWNERS

VERSION='campusflow.final-estimate.v1'


def _seal(entry,contract):
    from src.material_inbox import fingerprint
    return fingerprint(json.dumps(dict(estimate=entry['estimate'],item_id=entry['item_id'],
        work_id=entry['work_id'],effort_ledger=entry.get('effort_ledger'),effort_grounding=entry.get('effort_grounding'),quantity_claims=entry.get('quantity_claims',[]),contract=contract,
        **({'effort_provenance':entry['effort_provenance']} if entry.get('effort_provenance') is not None else {})),
        ensure_ascii=False,sort_keys=True))


def validate_final_estimate(entry):
    from src.material_inbox import MaterialError
    from src.material_estimate_recovery import validate_estimate,scope_clarification
    from src.estimate_arithmetic import ensure_estimate_arithmetic
    from src.material_requirements import evaluate_estimate_facts
    from src.material_formal_validation import fields,failure
    from src.material_estimate_recovery import ESTIMATE_KEYS,MAX_FALLBACK_MINUTES
    candidate=entry.get('estimate')
    fields(candidate,ESTIMATE_KEYS,'$.estimate')
    for name in ('focused_minutes_min','recommended_minutes','focused_minutes_max'):
        if type(candidate[name]) is not int or not 1<=candidate[name]<=MAX_FALLBACK_MINUTES:
            failure('invalid_duration_range','$.estimate.'+name,'integer 1..1440',candidate[name])
    if not candidate['focused_minutes_min']<=candidate['recommended_minutes']<=candidate['focused_minutes_max']:
        failure('invalid_duration_range','$.estimate.recommended_minutes','within min..max',candidate['recommended_minutes'])
    value=validate_estimate(candidate)
    contract=entry.get('final_contract')
    if (not isinstance(contract,dict) or contract.get('version')!=VERSION
            or not contract.get('source_fingerprint') or not entry.get('item_id') or not entry.get('work_id')
            or entry.get('final_contract_seal')!=_seal(entry,contract)):
        from src.material_formal_validation import invariant,shape
        invariant('final_estimate_identity_and_seal',
            ['$.final_contract','$.item_id','$.work_id','$.final_contract_seal'],
            'version, source identity and sealed final content must match',
            dict(contract=shape(contract),has_item_id=bool(entry.get('item_id')),
                 has_work_id=bool(entry.get('work_id')),seal_matches=isinstance(contract,dict) and entry.get('final_contract_seal')==_seal(entry,contract)))
    fields=contract.get('unresolved_required_fields')
    if not isinstance(fields,list) or any(f not in ('scope','identity','deadline','location','fixed_arrangement','existing_task','multiple_actions') for f in fields):
        from src.material_formal_validation import invariant,shape
        invariant('final_required_confirmation_fields',['$.final_contract.unresolved_required_fields'],
            'array of known unresolved required fields',shape(fields))
    # Local coarse estimates keep their existing feature ledger/validation.
    if entry.get('origin')!='local_workload':
        from src.material_effort import ensure_effort,digest
        ledger=entry.get('effort_ledger')
        if ledger and any('provenance_type' in r for r in ledger.get('adjustments',())) and entry.get('effort_provenance') is None:
            failure('unverified_effort_ledger','$.effort_provenance','verified source receipt required for referenced adjustments')
        if ledger and entry.get('effort_grounding')!=digest(ledger):
            from src.material_formal_validation import failure
            failure('unverified_effort_ledger','$.effort_grounding','previously grounded ledger')
        ensure_effort(value['recommended_minutes'],value['focused_minutes_min'],
            value['focused_minutes_max'],value['rationale'],value['assumptions'],ledger=ledger,
            sources=entry.get('effort_provenance'),stage='material_final_validation')
    errors,_,_,_=evaluate_estimate_facts(value['rationale'],value['assumptions'],
        contract['material_facts'],entry.get('quantity_claims'),contract['material_requirements'],stage='material_final_validation')
    if errors:failure(errors[0]['code'],'$.'+errors[0].get('field','quantity_claims'),'consistent with source facts')
    if scope_clarification(entry) and 'scope' not in fields:fields=fields+['scope']
    record('material_final_validation','final_estimate_contract','accepted',valid=True)
    return tuple(fields)


def complete_final_estimate(entry,draft,refs,responses,facts,requirements,has_formal_items=False):
    from src.material_inbox import parse_extraction,extract_json_object,AgenticParseError,MaterialError,fingerprint
    from src.material_estimate_recovery import _blocking_confirmation_fields,scope_clarification
    result=dict(entry)
    from src.material_requirement_binding import bind_estimate,bind_copy
    result['estimate']=bind_estimate(entry['estimate'],requirements,'rationale')
    _,_,bound_claims=bind_copy('',[],entry.get('quantity_claims',[]),requirements)
    from src.material_output_schema import authoritative_claims
    result['quantity_claims']=authoritative_claims(facts,requirements,bound_claims)
    last_error=None;canonical=None;current=None
    # Final current evidence determines confirmation; not an OR of past errors.
    if responses:
        try:
            current=extract_json_object(responses[-1])
            canonical=canonical_envelope(current)
            if entry.get('origin') in ('model_workload','local_workload'):
                # Independent source-backed workloads already have their own
                # formal identity. Recognition failures never invent task facts.
                canonical=dict(schema_version='campusflow.material-text.v2',reference_date=None,
                    reference_evidence=None,items=[],actionability=current.get('actionability'))
            else:
                # Validate the original envelope. A recovered estimate does not
                # publish incomplete formal rows; those remain user-confirmed.
                if not isinstance(canonical.get('items'),list) or len(canonical['items'])>12:
                    from src.material_formal_validation import failure
                    failure('wrong_type','$.items','array with at most 12 items',canonical.get('items'))
            parse_extraction(json.dumps(dict(canonical,items=[]),ensure_ascii=False),draft,refs)
        except (ValueError,TypeError,KeyError,AgenticParseError) as exc:
            last_error=exc
    if canonical is None or last_error:
        if last_error:record_formal_failure(last_error,'material_final_validation','recovery_envelope')
        # Preserve the estimate for display, without claiming it is adoptable.
        result['final_validation_errors']=[feedback(last_error)]
        result['confirmation_fields']=tuple(f for f in entry.get('confirmation_fields',()) if f!='details')
        result['scope_confirmation_allowed']=False
        return result
    unresolved=set(_blocking_confirmation_fields(current if entry.get('origin') in ('model_workload','local_workload') else canonical))
    if 'details' in unresolved:
        result['final_validation_errors']=[dict(code='invalid_confirmation_state',field_path='$.actionability.confirmation_required')]
        result['confirmation_fields']=tuple(sorted(unresolved-{'details'}))
        result['scope_confirmation_allowed']=False
        return result
    if has_formal_items:unresolved.add('multiple_actions')
    if scope_clarification(entry):unresolved.add('scope')
    claims=result['quantity_claims']
    result.update(quantity_claims=claims,
        work_id=entry.get('work_id') or fingerprint(draft.source_fingerprint,entry['estimate']['task_name'],entry['estimate']['short_scope'])[:24],
        origin=entry.get('origin','model_recovery'),confirmation_fields=tuple(sorted(unresolved)),
        scope_confirmation_allowed=not (unresolved-{'scope'}),final_validation_errors=[])
    contract=dict(version=VERSION,source_fingerprint=draft.source_fingerprint,
        material_facts=facts,material_requirements=requirements,unresolved_required_fields=sorted(unresolved))
    result['final_contract']=contract
    result['final_contract_seal']=_seal(result,contract)
    try:validate_final_estimate(result)
    except (ValueError,TypeError,KeyError,MaterialError) as exc:
        record_formal_failure(exc,'material_final_validation','final_estimate')
        result.pop('final_contract',None);result.pop('final_contract_seal',None)
        result['final_validation_errors']=[feedback(exc)]
        result['scope_confirmation_allowed']=False
    return result


def refresh_final_estimate(entry):
    """Revalidate after an official reestimate, retaining required constraints."""
    if not entry.get('final_contract'):
        return entry
    result=dict(entry)
    contract=dict(entry['final_contract'],unresolved_required_fields=sorted(
        set(entry['final_contract']['unresolved_required_fields']) |
        set(entry.get('confirmation_fields',()))))
    result['final_contract']=contract
    result['final_contract_seal']=_seal(result,contract)
    validate_final_estimate(result)
    return result
