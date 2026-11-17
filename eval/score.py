#!/usr/bin/env python3
"""Score generated answers with an OpenAI-compatible semantic judge.

Input is an eval.py output JSON array. The judge receives only the reference and
model answer, and must return exactly yes/no. Results are checkpointed after each
sample and include per-turn accuracy plus an overall accuracy.
"""
import argparse
import re
from pathlib import Path

try:
    from .common import ROOT, add_api_args, make_client, read_json, save_json
except ImportError:
    from common import ROOT, add_api_args, make_client, read_json, save_json

JUDGE_PROMPT = """Compare the following two texts: a reference text and a generated text. Determine whether the generated text correctly
conveys the same meaning as the reference text. Do not require the wording to be identical—focus on whether the
semantics, key facts, and intent are preserved. Minor differences in phrasing or style are acceptable. Respond with a
judgment of correct if the generated text is semantically equivalent to the reference text; otherwise, it’s wrong.
Output ‘yes’ if correct, ‘no’ if wrong. Output only ‘yes’ or ‘no’ without any additional text.

Reference text:
{reference}

Generated text:
{generated}"""


def parse_yes_no(text):
    match = re.fullmatch(r'(yes|no)', text.strip().lower()) if isinstance(text, str) else None
    return match.group(1) if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='JSON output produced by eval.py')
    parser.add_argument('--output', type=Path, help='Defaults to <input stem>_scores.json')
    parser.add_argument('--model', required=True, help='Judge model name')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-tokens', type=int, default=8)
    add_api_args(parser)
    args = parser.parse_args()
    rows = read_json(args.input)
    if not isinstance(rows, list) or not rows:
        raise ValueError('Input must be a non-empty JSON array.')
    if any(not r.get('model_response') or r.get('error') for r in rows):
        raise ValueError('Input contains missing/failed model responses; fix eval output first.')
    if len({r['sample_id'] for r in rows}) != len(rows):
        parser.error('Input contains duplicate sample_id values')
    if any(not isinstance(r.get('ground_truth'), str) or not r['ground_truth'].strip() for r in rows):
        parser.error('Each response must contain a non-empty ground_truth')
    metadata = {'judge_model': args.model, 'num_samples': len(rows),
                'source_file': str(args.input.resolve())}
    for source_key, key in (('model', 'evaluated_model'), ('split', 'split')):
        values = {r.get(source_key) for r in rows}
        if len(values) != 1:
            parser.error(f'Input contains mixed {source_key} values')
        metadata[key] = values.pop()
    output = args.output or args.input.with_name(args.input.stem + '_scores.json')
    if output.resolve() == args.input.resolve():
        parser.error('--output cannot overwrite the input responses')
    if output.exists() and not args.resume:
        parser.error(f'{output} exists; use --resume or choose another --output')
    saved = read_json(output) if output.exists() else {'judge_model': args.model, 'results': []}
    if saved.get('judge_model') != args.model:
        parser.error('Checkpoint judge model differs from this run')
    for key in ('evaluated_model', 'split'):
        if saved.get(key) is not None and saved[key] != metadata[key]:
            parser.error(f'Checkpoint {key} differs from this run')
    results = {r['sample_id']: r for r in saved.get('results', [])}
    if not set(results).issubset({r['sample_id'] for r in rows}):
        parser.error('Checkpoint includes samples outside the current input')
    client = None
    from tqdm import tqdm
    for row in tqdm(rows, desc='Scoring'):
        sid = row['sample_id']
        if sid in results and results[sid].get('judgment') in ('yes', 'no'):
            continue
        prompt = JUDGE_PROMPT.format(reference=row['ground_truth'], generated=row['model_response'])
        try:
            if client is None:
                client = make_client(args)
            completion = client.chat.completions.create(
                model=args.model,
                messages=[{'role': 'user', 'content': prompt}],
                max_tokens=args.max_tokens, temperature=0)
            raw = completion.choices[0].message.content
            judgment = parse_yes_no(raw)
            if judgment is None:
                raise ValueError(f'Judge did not return yes/no: {raw!r}')
            results[sid] = {'sample_id': sid, 'dialogueID': row.get('dialogueID'), 'turn': row['turn'], 'judgment': judgment, 'raw_judgment': raw}
        except Exception as exc:
            results[sid] = {'sample_id': sid, 'dialogueID': row.get('dialogueID'), 'turn': row['turn'], 'judgment': None, 'error': f'{type(exc).__name__}: {exc}'}
        ordered = list(results.values())
        save_json(output, {**metadata, 'results': ordered})
    scored = list(results.values())
    valid = [r for r in scored if r.get('judgment') in ('yes', 'no')]
    by_turn = {}
    for r in valid:
        by_turn.setdefault(str(r['turn']), []).append(r['judgment'] == 'yes')
    accuracy_by_turn = {t: sum(v) / len(v) for t, v in by_turn.items()}
    summary = {**metadata, 'num_scored': len(valid), 'num_failed': len(scored) - len(valid),
               'accuracy': (sum(r['judgment'] == 'yes' for r in valid) / len(valid) if valid else None),
               'accuracy_by_turn': accuracy_by_turn}
    summary['results'] = scored
    save_json(output, summary)
    print(f"Accuracy: {summary['accuracy']:.4f}" if summary['accuracy'] is not None else 'Accuracy unavailable')
    for t, acc in sorted(summary['accuracy_by_turn'].items(), key=lambda x: int(x[0])): print(f'Turn-{t}: {acc:.4f}')
    if len(valid) < len(rows):
        print(f'{len(scored) - len(valid)} failed judgments; accuracy above is provisional. Rerun with --resume.')
        raise SystemExit(1)
    print(f'Saved scores to {output}. Use eval/aggregate.py to compute cross-turn VFR and SRS.')


if __name__ == '__main__':
    main()
