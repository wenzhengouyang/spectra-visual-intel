#!/usr/bin/env python3
"""Interactive human input for the existing localization validation command."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def ask_with_default(label, default):
    value = input(f'{label}\n候选：{default}\n直接回车采用候选，或输入修改稿：').strip()
    return value or default

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    path = run_dir / 'p2-localization-review.json'
    state = json.loads((run_dir / 'run.json').read_text())
    if state.get('current_stage') != 'localization_review':
        print('流程已不在中文化审核阶段，请返回工作台确认。')
        return
    queue = json.loads(path.read_text())
    print('\nSPECTRA 中文化审核 — ' + run_dir.name)
    print('逐条确认候选中文稿；直接回车采用候选，输入修改稿可编辑。')
    print('输入 skip、/skip 或 s 均为暂时跳过；Ctrl+C 退出。')
    for item in queue.get('records', []):
        if item.get('review_status') == 'approved':
            continue
        print('\n' + '─' * 55)
        print('条目：', item['brief_id'])
        print('原始标题：', item.get('original_headline', ''))
        print('原始摘要：', item.get('original_dek', ''))
        for risk in item.get('fact_risks', []):
            label = '数字或单位' if risk.get('risk_type') == 'number_or_unit' else '归因或不确定性'
            print(f"事实风险（{label} / {risk.get('field')}）：")
            print('  原文：', risk.get('source', ''))
            print('  候选：', risk.get('candidate', ''))
        if not item.get('fact_risks'):
            print('拦截原因：', '；'.join(item.get('errors', [])))
        while True:
            suggestion = (
                item.get('candidate_headline_zh') or item.get('headline_zh') or '',
                item.get('candidate_dek_zh') or item.get('dek_zh') or '',
            )
            headline = ask_with_default('\n中文标题', suggestion[0]) if suggestion[0] else input('\n中文标题：').strip()
            if headline.lower() in {'skip', '/skip', 's'}:
                break
            dek = ask_with_default('中文摘要', suggestion[1]) if suggestion[1] else input('中文摘要：').strip()
            if dek.lower() in {'skip', '/skip', 's'}:
                break
            if not headline or not dek:
                print('标题与摘要均不能为空。')
                continue
            print('\n待提交标题：', headline, '\n待提交摘要：', dek)
            if input('确认提交这条？输入 y 确认，其他键重新编辑：').strip().lower() != 'y':
                continue
            result = subprocess.run([
                sys.executable, str(ROOT / 'processor/apply_localization_review.py'),
                '--run-dir', str(run_dir), '--brief-id', item['brief_id'],
                '--headline', headline, '--dek', dek, '--reviewer', 'human-terminal',
            ], capture_output=True, text=True)
            if result.returncode == 0:
                print('已通过校验并保存你的审核。')
                break
            reason = (result.stderr or result.stdout).strip().splitlines()[-1]
            if 'not a readable Chinese sentence' in reason:
                reason = '标题需要是一句以中文为主、可直接阅读的话。'
            elif 'numbers/units' in reason:
                reason = '标题或摘要中的数字、单位与原文不一致。'
            print('校验未通过，未批准。原因：' + reason)
            print('请修改，或输入 skip 暂留。')
    latest = json.loads(path.read_text())
    pending = sum(x.get('review_status') != 'approved' for x in latest.get('records', []))
    if pending:
        print(f'\n本轮结束，仍有 {pending} 条待审核；流程保持暂停，不会越过人工审核。')
    else:
        print('\n中文化审核已全部完成，正在自动恢复后续生成、预览和发布流程。')
        result = subprocess.run([
            sys.executable, str(ROOT / 'spectra_agent/run.py'), '--config',
            'spectra_agent/config.v0.1.json', 'resume', '--run-id', run_dir.name, '--retry',
        ], cwd=ROOT)
        if result.returncode:
            print('自动恢复失败，请保留此窗口并查看上方错误。')
    input('按回车结束。')

if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\n已退出，未提交的内容不作批准。')
