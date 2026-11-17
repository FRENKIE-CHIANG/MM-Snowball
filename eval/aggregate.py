#!/usr/bin/env python3
"""Aggregate saved judge results into per-turn accuracy, VFR and SRS (no API calls)."""
import argparse
from pathlib import Path

try:
    from .common import read_json, save_json
except ImportError:
    from common import read_json, save_json


def calculate_metrics(results, expected_count=2000):
    """Require complete judgments on the same dialogue IDs in every supplied turn."""
    if not results:
        raise ValueError('No judgments provided.')
    if expected_count < 1:
        raise ValueError('expected_count must be positive.')
    by_turn = {}
    sample_ids = set()
    for row in results:
        turn = row.get('turn')
        dialogue_id = row.get('dialogueID')
        sample_id = row.get('sample_id')
        if type(turn) is not int or turn not in range(1, 7):
            raise ValueError(f'Invalid turn: {turn!r}')
        if type(dialogue_id) is not int or not isinstance(sample_id, str) or not sample_id:
            raise ValueError('Each judgment requires an integer dialogueID and non-empty sample_id.')
        if row.get('error') or row.get('judgment') not in ('yes', 'no'):
            raise ValueError(f'Missing/failed judgment for {sample_id}; resume scoring first.')
        group = by_turn.setdefault(turn, {})
        if sample_id in sample_ids or dialogue_id in group:
            raise ValueError(f'Duplicate judgment for dialogue {dialogue_id}, turn {turn}.')
        sample_ids.add(sample_id)
        group[dialogue_id] = row['judgment'] == 'yes'
    baseline_ids = set(next(iter(by_turn.values())))
    for turn, group in by_turn.items():
        if len(group) != expected_count:
            raise ValueError(f'Turn-{turn} has {len(group)} dialogues; expected {expected_count}.')
        if set(group) != baseline_ids:
            raise ValueError(f'Turn-{turn} has different dialogue IDs; all turns must use the same split/subset.')

    accuracies = {str(t): sum(group.values()) / expected_count for t, group in sorted(by_turn.items())}
    summary = {
        'num_dialogues': expected_count,
        'num_samples': len(results),
        'turns': sorted(by_turn),
        'accuracy_by_turn': accuracies,
        'accuracy_percent_by_turn': {t: acc * 100 for t, acc in accuracies.items()},
        'vfr_percent': None,
        'srs_percent': None,
        'unavailable_metrics': {},
    }
    if 1 in by_turn and 5 in by_turn:
        if accuracies['1'] == 0:
            summary['unavailable_metrics']['vfr_percent'] = 'Turn-1 accuracy is zero; VFR is undefined.'
        else:
            summary['vfr_percent'] = (accuracies['1'] - accuracies['5']) / accuracies['1'] * 100
    else:
        summary['unavailable_metrics']['vfr_percent'] = 'Requires complete Turn-1 and Turn-5 judgments.'
    if all(t in by_turn for t in (3, 4, 5)):
        successes = sum(all(by_turn[t][d] for t in (3, 4, 5)) for d in baseline_ids)
        summary['srs_num_successes'] = successes
        summary['srs_percent'] = successes / expected_count * 100
    else:
        summary['unavailable_metrics']['srs_percent'] = 'Requires complete Turn-3, Turn-4 and Turn-5 judgments.'
    return summary


def aggregate_files(paths, expected_count=2000):
    results = []
    metadata = {key: set() for key in ('judge_model', 'evaluated_model', 'split')}
    missing = {key: [] for key in metadata}
    for path in paths:
        data = read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get('results'), list):
            raise ValueError(f'{path}: expected a score.py output object containing results.')
        if data.get('num_samples') is not None and len(data['results']) != data['num_samples']:
            raise ValueError(f'{path}: checkpoint is incomplete; resume scoring first.')
        for key in metadata:
            value = data.get(key)
            if value is None:
                missing[key].append(str(path))
            else:
                metadata[key].add(value)
        results.extend(data['results'])
    for key, values in metadata.items():
        if len(values) > 1:
            raise ValueError(f'Input files have different {key} values: {sorted(values)}')
    summary = calculate_metrics(results, expected_count)
    for key, values in metadata.items():
        summary[key] = next(iter(values)) if values and not missing[key] else None
    summary['metadata_warnings'] = [
        f'{key} is missing in {len(files)} input file(s); consistency cannot be fully verified. '
        'For older score files, rerun score.py with --resume to refresh metadata.'
        for key, files in missing.items() if files
    ]
    summary['score_files'] = [str(p.resolve()) for p in paths]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, nargs='+', required=True, help='One or more *_scores.json files.')
    parser.add_argument('--output', type=Path, required=True, help='Summary JSON path; may be regenerated.')
    parser.add_argument('--expected-count', type=int, default=2000,
                        help='Expected number of dialogues in each supplied turn (test: 2000, train: 2992).')
    args = parser.parse_args()
    if args.output.resolve() in {p.resolve() for p in args.input}:
        parser.error('--output cannot overwrite an input score file')
    try:
        summary = aggregate_files(args.input, args.expected_count)
    except ValueError as exc:
        parser.error(str(exc))
    save_json(args.output, summary)
    for turn, accuracy in summary['accuracy_percent_by_turn'].items():
        print(f'Turn-{turn} accuracy: {accuracy:.2f}%')
    for name, key in (('VFR', 'vfr_percent'), ('SRS', 'srs_percent')):
        if summary[key] is not None:
            print(f'{name}: {summary[key]:.2f}%')
        else:
            print(f'{name}: unavailable ({summary["unavailable_metrics"][key]})')
    for warning in summary['metadata_warnings']:
        print(f'Warning: {warning}')
    print(f'Saved summary to {args.output}')


if __name__ == '__main__':
    main()
