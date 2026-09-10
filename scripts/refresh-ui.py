#!/usr/bin/env python3
"""Render the shared report shell with an existing issue; no editorial mutation or send."""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spectra_agent.run import prepare_static_draft, validate_static_package


def render(issue, output, source_dir=None):
    shell = (ROOT / 'visual-intelligence-prototype.html').read_text()
    payload = json.dumps(issue, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    markup = '<!-- ISSUE_DATA_START --><script id="issue-data" type="application/json">' + payload + '</script><!-- ISSUE_DATA_END -->'
    shell, count = re.subn(r'<!-- ISSUE_DATA_START -->.*?<!-- ISSUE_DATA_END -->', lambda _: markup, shell, flags=re.S)
    if count != 1:
        raise ValueError('Expected exactly one embedded issue')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent != ROOT:
        prepare_static_draft(output.parent, ROOT / 'visual-intelligence-prototype.html')
    if source_dir:
        for story in issue.get('editorial_stories', []):
            cover = (story.get('cover_image') or {}).get('url', '')
            if cover.startswith('assets/') and '..' not in Path(cover).parts:
                source = source_dir / cover
                target = output.parent / cover
                if source.is_file() and source.resolve() != target.resolve():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    output.write_text(shell)
    validate_static_package(output.parent, output, issue)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issue', required=True)
    parser.add_argument('--output', default=str(ROOT / 'index.html'))
    args = parser.parse_args()
    render(json.loads(Path(args.issue).read_text()), Path(args.output).resolve(), Path(args.issue).resolve().parent)
    print('Rendered and validated: ' + args.output)
