"""Small shared helpers for the command-line evaluation tools."""
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, data):
    """Atomically replace a checkpoint so interruptions cannot truncate it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
        temporary = f.name
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(temporary, path)


def safe_name(value):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', value).strip('_') or 'model'


def make_client(args):
    from openai import OpenAI
    key = args.api_key or os.getenv('OPENAI_API_KEY')
    if not key:
        raise ValueError('Set OPENAI_API_KEY (a dummy value is OK for a local server).')
    return OpenAI(api_key=key, base_url=args.base_url or os.getenv('OPENAI_BASE_URL'),
                  timeout=args.timeout, max_retries=args.max_retries)


def add_api_args(parser):
    parser.add_argument('--api-key', help='Defaults to OPENAI_API_KEY; prefer the environment variable.')
    parser.add_argument('--base-url', help='OpenAI-compatible endpoint; defaults to OPENAI_BASE_URL.')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--max-retries', type=int, default=3)
