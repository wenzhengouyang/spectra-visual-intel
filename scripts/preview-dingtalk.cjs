// Render the actual Markdown payload locally; never sends a DingTalk message.
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.SPECTRA_PLAYWRIGHT || 'playwright');
const root=path.resolve(__dirname,'..');
const payload=JSON.parse(execFileSync(path.join(root,'.venv-llm/bin/python'),['-c',
  'import json; from pathlib import Path; from spectra_agent.dingtalk_push import notification_payload; issue=json.loads(Path("/Users/wenzheng/Library/Application Support/SPECTRA/data/runs/daily-20260909/editorial-issue.json").read_text()); print(json.dumps(notification_payload(issue,"https://wenzhengouyang.github.io/spectra-visual-intel/","https://example.com/compact-header.png")))'
],{cwd:root,encoding:'utf8'}));
const escape=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const banner='file://'+root+'/assets/dingtalk/spectra-compact-header.svg';
const html=payload.markdown.text.split(/\n\n+/).map(block=>{
  if(block.startsWith('!['))return `<img class="banner" src="${banner}">`;
  if(block==='---')return '<hr>';
  let text=escape(block).replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2">$1</a>');
  if(text.startsWith('&gt; '))return '<blockquote>'+text.slice(5)+'</blockquote>';
  return '<p>'+text+'</p>';
}).join('');
(async()=>{
 const b=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
  const p=await b.newPage({viewport:{width:620,height:1100},deviceScaleFactor:1});
  await p.goto('file://'+root+'/index.html');
  await p.setContent(`<style>*{box-sizing:border-box}body{margin:0;padding:28px;background:#edf1f6;color:#283544;font-family:'PingFang SC',sans-serif}header{margin:0 0 20px}header strong{font-size:18px}header small{display:block;color:#738195;font-size:12px;margin-top:8px}.message{border:1px solid #dfe5ed;background:white;border-radius:12px;padding:22px}.banner{display:block;width:100%;height:auto;border-radius:8px;margin:0 0 20px}p{margin:20px 0;font-size:15px;line-height:1.85}p:first-of-type{font-size:12px;color:#7b8693;letter-spacing:.5px;margin:0 0 26px}b{font-weight:600}a{color:#2476cf;text-decoration:none}blockquote{font-size:15px;line-height:1.8;margin:16px 0 20px;padding:0 0 0 14px;border-left:3px solid #c5d3e1;color:#65778c}hr{border:0;border-top:1px solid #e7ebf1;margin:24px 0}.message>p:last-child{margin:20px 0 0}footer{font-size:11px;color:#7e8b9b;line-height:1.7;margin-top:18px}</style><header><strong>钉钉消息 · 轻量蓝色版</strong><small>现有发送模板预览 · 沿用 9 月 9 日数据</small></header><div class="message">${html}</div><footer>模拟原生 Markdown 排版；具体字号、间距与链接颜色由钉钉客户端决定。<br>蓝色标题可点击查看原文。头图不再包含固定日期。</footer>`);
  await p.locator('img').evaluateAll(imgs=>Promise.all(imgs.map(i=>i.decode())));
  await p.screenshot({path:'/tmp/spectra-dingtalk-compact-preview.png',fullPage:true});
  await p.setViewportSize({width:1200,height:220});
  await p.setContent(`<style>body{margin:0}img{display:block;width:1200px;height:220px}</style><img src="${banner}">`);
  await p.locator('img').evaluate(i=>i.decode());
  await p.screenshot({path:root+'/assets/dingtalk/spectra-compact-header.png'});
  console.log('Preview: /tmp/spectra-dingtalk-compact-preview.png');
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1)});
