#!/usr/bin/env python3
"""Evaluate a vision model using the benchmark's fixed hallucinated history."""
import argparse
import base64
import mimetypes
from pathlib import Path

try:
    from .common import ROOT, add_api_args, make_client, read_json, safe_name, save_json
except ImportError:
    from common import ROOT, add_api_args, make_client, read_json, safe_name, save_json


def load_samples(path, turn):
    rows = read_json(path)
    if not isinstance(rows, list):
        raise ValueError('Dataset must be a JSON array.')
    samples = []
    for row in rows:
        if 'dialogue' in row:
            qa = row['dialogue'][f'turn_{turn}']
            sample = {k: row[k] for k in ('dialogueID', 'image_path', 'caption')}
            sample.update(
                sample_id=f"{row['dialogueID']}_turn_{turn}",
                turn=turn,
                history=[
                    {
                        'turn': t,
                        'question': row['dialogue'][f'turn_{t}']['question'],
                        'hallucinated_answer': row['dialogue'][f'turn_{t}']['hallucinated_answer'],
                    }
                    for t in range(1, turn)
                ],
                question=qa['question'],
                ground_truth=qa['correct_answer'],
            )
        else:
            sample = row
        if 'split' in sample and sample['split'] not in ('test', 'train'):
            raise ValueError(f"Invalid split for {sample['sample_id']}: {sample['split']!r}")
        if sample['turn'] != turn:
            raise ValueError(f"Input contains Turn-{sample['turn']}; requested Turn-{turn}.")
        if len(sample['history']) != turn - 1:
            raise ValueError(f"Invalid history length for {sample['sample_id']}")
        samples.append(sample)
    if len({s['sample_id'] for s in samples}) != len(samples):
        raise ValueError('Duplicate sample_id values.')
    return samples


def build_messages(image_path, history, question):
    mime = mimetypes.guess_type(image_path.name)[0] or 'image/jpeg'
    image_b64 = base64.b64encode(image_path.read_bytes()).decode('ascii')
    messages = []
    for qa in history:
        messages.extend([{'role': 'user', 'content': qa['question']},
                         {'role': 'assistant', 'content': qa['hallucinated_answer']}])
    messages.append({'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{image_b64}'}},
        {'type': 'text', 'text': question},
    ]})
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--turn', type=int, required=True, choices=range(1, 7))
    parser.add_argument('--model', required=True)
    parser.add_argument('--split', choices=('test', 'train'), default='test',
                        help='Use data/test or data/train when --input is omitted (default: test).')
    parser.add_argument('--input', type=Path, help='Defaults to data/<split>/turn-N.json; original JSON is also supported.')
    parser.add_argument('--dataset-root', type=Path, default=ROOT, help='Directory containing images/.')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--limit', type=int, help='Optional smoke-test subset.')
    parser.add_argument('--max-tokens', type=int, default=1024)
    parser.add_argument('--temperature', type=float, default=0)
    parser.add_argument('--resume', action='store_true', help='Reuse successful results; retry failed records.')
    parser.add_argument('--dry-run', action='store_true', help='Validate all selected samples/images without API calls.')
    add_api_args(parser)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    source = args.input or ROOT / 'data' / args.split / f'turn-{args.turn}.json'
    samples = load_samples(source, args.turn)
    if args.limit:
        samples = samples[:args.limit]
    image_paths = {}
    for s in samples:
        path = Path(s['image_path'])
        # Original release paths may contain a machine-specific absolute prefix.
        path = args.dataset_root / 'images' / path.name if path.is_absolute() else args.dataset_root / path
        if not path.is_file():
            raise FileNotFoundError(path)
        image_paths[s['sample_id']] = path
    if args.dry_run:
        print(f'Validated {len(samples):,} Turn-{args.turn} samples and images.')
        return
    split_suffix = '_train' if args.split == 'train' and args.input is None else ''
    output = args.output or ROOT / 'eval_outputs' / f'{safe_name(args.model)}{split_suffix}_Turn-{args.turn}.json'
    if output.resolve() == source.resolve():
        parser.error('--output cannot overwrite the input dataset')
    if output.exists() and not args.resume:
        parser.error(f'{output} exists; use --resume or choose another --output')
    results = read_json(output) if output.exists() else []
    if any(r.get('model') != args.model or r.get('turn') != args.turn for r in results):
        parser.error('Checkpoint model/turn differs from this run')
    by_id = {r['sample_id']: r for r in results}
    client = make_client(args)
    from tqdm import tqdm
    for sample in tqdm(samples, desc=f'Turn-{args.turn}'):
        sid = sample['sample_id']
        if sid in by_id and not by_id[sid].get('error') and isinstance(by_id[sid].get('model_response'), str) and by_id[sid]['model_response'].strip():
            continue
        record = {**sample, 'model': args.model}
        try:
            completion = client.chat.completions.create(
                model=args.model,
                messages=build_messages(image_paths[sid], sample['history'], sample['question']),
                max_tokens=args.max_tokens, temperature=args.temperature)
            response = completion.choices[0].message.content
            if not isinstance(response, str) or not response.strip():
                raise ValueError('API returned an empty or non-text response')
            record['model_response'] = response
        except Exception as exc:
            record.update(model_response=None, error=f'{type(exc).__name__}: {exc}')
            tqdm.write(f'{sid}: {record["error"]}')
        by_id[sid] = record
        save_json(output, list(by_id.values()))
    failed = sum(bool(r.get('error')) for r in by_id.values())
    print(f'Saved {len(by_id):,} records to {output} ({failed} failed; rerun with --resume).')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
