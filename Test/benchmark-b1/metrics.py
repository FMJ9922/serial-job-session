"""Ancillary metrics from immutable B1 deliveries; never changes scores.

Token counts are reported cumulative counters, deduplicated by session ID.
Reasoning tokens and cached tokens are subsets, not additional total tokens.
Unreported or inconsistent quantities remain null, including actual fees.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import runner
import closed_seal

TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def session_metrics(root, files=None):
    sessions = {}
    warnings = []
    variants = {}
    for path in sorted(Path(root).rglob('*.jsonl') if files is None else files):
        try:
            rows = list(closed_seal.json_events(path, with_line=True))
            first = rows[0][1] if rows else {}
            payload = first.get('payload', {})
            session_id = payload.get('id', payload.get('session_id')) if isinstance(payload, dict) else None
            if first.get('type') != 'session_meta' or not isinstance(session_id, str) or not session_id:
                raise RuntimeError('Missing first-session identity anchor')
        except (RuntimeError, ValueError) as exc:
            warnings.append(f'Unattributable session trace: {path.name}: {exc}')
            continue
        session = sessions.setdefault(session_id, {'events': {}, 'files': [], 'source': payload.get('source'),
                                                  'attribution_verified': False})
        session['files'].append(path.name)
        variants.setdefault(session_id, []).append((path, rows))
    # Reuse the same reviewed first-owner, ordinal-boundary and legal ancestor
    # projection checks as closure. Never infer an execution owner from a later
    # inherited session_meta or from timestamps alone.
    representatives = {sid: max(items, key=lambda item: len(item[1])) for sid, items in variants.items()}
    try:
        nodes = closed_seal.rollout_own_streams([item[0] for item in representatives.values()]) if representatives else {}
        for sid, items in variants.items():
            relevant = []
            for path, rows in items:
                representative, longest = representatives[sid]
                # Exact earlier snapshots of a resumed owner add no events.
                if rows == longest[:len(rows)]:
                    node = nodes[sid]
                else:
                    bundle = [path if other == sid else item[0] for other, item in representatives.items()]
                    node = closed_seal.rollout_own_streams(bundle)[sid]
                if (node['parent'], node['boundary']) != (nodes[sid]['parent'], nodes[sid]['boundary']):
                    raise RuntimeError('Conflicting parent or execution boundary across resumed owner files')
                relevant.extend(event for _, event in node['own'])
            for event in relevant:
                kind, payload = event['type'], event['payload']
                if kind in ('turn_context', 'compacted') or (
                        kind == 'event_msg' and payload.get('type') in ('token_count', 'context_compacted')):
                    key = json.dumps(event, sort_keys=True, ensure_ascii=False)
                    sessions[sid]['events'][key] = event
            sessions[sid]['attribution_verified'] = True
    except (RuntimeError, ValueError) as exc:
        warnings.append('Unknown own-execution attribution; counters/compactions withheld: ' + str(exc))
        for session in sessions.values():
            session['events'].clear()
            session['attribution_verified'] = False
    attribution_complete = not warnings and all(session['attribution_verified'] for session in sessions.values())

    result = []
    if not sessions:
        warnings.append('No session traces available.')
    for session_id, session in sorted(sessions.items()):
        events = sorted(session['events'].values(), key=lambda event: event.get('timestamp', ''))
        counters = []
        contexts = []
        primary_compactions = fallback_compactions = 0
        source = session['source']
        for event in events:
            kind = event['type']
            payload = event.get('payload', {})
            if kind == 'turn_context':
                contexts.append(payload)
            elif kind == 'compacted':
                primary_compactions += 1
            elif kind == 'event_msg' and payload.get('type') == 'context_compacted':
                fallback_compactions += 1
            elif kind == 'event_msg' and payload.get('type') == 'token_count':
                total = (payload.get('info') or {}).get('total_token_usage')
                if isinstance(total, dict):
                    counters.append(total)
        tokens = {}
        resets = []
        for field in TOKEN_FIELDS:
            values = [counter[field] for counter in counters if field in counter]
            valid = (values and field in counters[-1]
                     and all(type(value) is int and value >= 0 for value in values))
            if valid and any(after < before for before, after in zip(values, values[1:])):
                resets.append(field)
                tokens[field] = None
            else:
                tokens[field] = values[-1] if valid else None
        if resets:
            warnings.append(f'Nonmonotonic cumulative counters in {session_id}: {", ".join(resets)}')
        if not counters:
            warnings.append('No reported cumulative usage in ' + session_id)
        models = sorted({context['model'] for context in contexts if context.get('model')})
        efforts = sorted({context.get('effort', context.get('reasoning_effort')) for context in contexts
                          if context.get('effort', context.get('reasoning_effort'))})
        result.append({'session_id': session_id, 'files': session['files'], 'source': source,
                       'models': models, 'efforts': efforts,
                       'own_execution_attribution_verified': session['attribution_verified'],
                       'recorded_turn_contexts': len(contexts) if session['attribution_verified'] else None,
                       'recorded_compaction_events': (primary_compactions or fallback_compactions) if session['attribution_verified'] else None,
                       'compaction_count_source': 'compacted' if primary_compactions else 'context_compacted',
                       'reported_token_totals': tokens})
    totals = {field: sum(session['reported_token_totals'][field] for session in result)
              if result and not warnings and all(session['reported_token_totals'][field] is not None for session in result)
              else None for field in TOKEN_FIELDS}
    return {'sessions': result, 'reported_token_totals': totals, 'warnings': warnings,
            'recorded_compaction_events': sum(session['recorded_compaction_events'] for session in result)
                if result and attribution_complete else None,
            'own_execution_attribution_complete': bool(result) and attribution_complete,
            'usage_scope': 'Recorded development CLI sessions and descendants only; excludes judge, preflight, and external asset-service usage.',
            'token_definition': 'Sum of latest monotonic cumulative counters for each distinct session; cached input and reasoning output are subsets.'}


def resume_metrics(state):
    """Classify started attempts, keeping policy decisions separate from outages."""
    reviews = state.get('infrastructure_reviews', [])
    approval = state.get('user_policy_resume_approval')
    result = {'infrastructure_review_count': len(reviews),
              'user_policy_approval_count': int(isinstance(approval, dict) and bool(approval)),
              'excluded_outage_seconds': sum(review['outage_seconds'] for review in reviews),
              'infrastructure_resume_count': None, 'user_policy_resume_count': None,
              'unclassified_resume_count': None,
              'resume_count_source': 'Unavailable: no recorded attempt history.'}
    attempts = state.get('attempts')
    if not isinstance(attempts, list) or not attempts:
        return result
    counts = {'infrastructure_approved_attempt': 0, 'user_policy_resume_approval': 0, 'unknown': 0}
    inferred = 0
    for previous, attempt in zip(attempts, attempts[1:]):
        basis = attempt.get('resume_basis')
        if basis is None:
            # Before runtime004 only infrastructure adjudication could resume.
            # Require a matching review rather than assuming every extra attempt
            # or approval was an actual infrastructure continuation.
            if any(review.get('attempt') == previous.get('number') and previous.get('number') is not None
                   for review in reviews):
                basis = 'infrastructure_approved_attempt'
                inferred += 1
            else:
                basis = 'unknown'
        counts[basis if basis in counts else 'unknown'] += 1
    result.update(infrastructure_resume_count=counts['infrastructure_approved_attempt'],
                  user_policy_resume_count=counts['user_policy_resume_approval'],
                  unclassified_resume_count=counts['unknown'],
                  legacy_infrastructure_resumes_inferred=inferred,
                  resume_count_source='Started attempt history; explicit resume_basis, or legacy matching prior-attempt infrastructure review.')
    return result


def collect_delivery(sealed):
    sealed = Path(sealed)
    seal = json.loads((sealed / 'seal.json').read_text(encoding='utf-8'))
    actual = runner.inventory(sealed)
    actual.pop('seal.json', None)
    if actual != seal['all_files']:
        raise ValueError('Sealed evidence changed: inventory mismatch')
    for relative, expected in seal['all_files'].items():
        if runner.digest(runner.check_links(sealed / relative)) != expected:
            raise ValueError('Sealed evidence changed: ' + relative)
    state = json.loads((sealed / 'state.json').read_text(encoding='utf-8'))
    if state['status'] not in runner.TERMINAL or not state.get('process_tree_stopped'):
        raise ValueError('Metrics require a stopped terminal delivery')
    session_files = [sealed / relative for relative in seal['all_files']
                     if relative.startswith('sessions/') and relative.endswith('.jsonl')]
    result = session_metrics(sealed / 'sessions', files=session_files)
    result.update(group=state['group'], termination=state['status'],
                  effective_seconds=state.get('effective_seconds'),
                  recorded_child_sessions=state.get('observations', {}).get('child_session_count'),
                  actual_cost=None, rework_count=None,
                  missing_metrics_reason={'actual_cost': 'No authoritative billed amount available.',
                                          'rework_count': 'No shared objective rework-event counter exists across treatments.'},
                  seal_sha256=runner.digest(sealed / 'seal.json'))
    result.update(resume_metrics(state))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin', type=Path, default=runner.DEFAULT_ADMIN)
    args = parser.parse_args()
    manifest = runner.manifest(args.admin)
    results = {}
    for key in manifest['order']:
        sealed = args.admin / 'sealed' / key
        if not (sealed / 'seal.json').is_file():
            raise RuntimeError('All six deliveries must be sealed before final ancillary reporting')
        results[key] = collect_delivery(sealed)
    output = args.admin / 'results/ancillary-metrics.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    runner.write_json(output, {'version': 'B1', 'groups': results, 'affects_main_score': False})
    print(str(output))


if __name__ == '__main__':
    main()
