/* Share reader-facing content; local previews share the public source. */
(() => {
  if (typeof bundle === 'undefined' || !bundle) return;
  function makeButton(id) {
    const button = document.createElement('button');
    button.className='share-button'; button.dataset.shareId=id;
    button.type='button'; button.textContent='分享 ↗';
    button.setAttribute('aria-label','分享这条情报'); return button;
  }
  function decorate() {
    document.querySelectorAll('[data-story-card], [data-share-brief]').forEach(card=>{
      if (!card.querySelector('[data-share-id]')) card.querySelector('.story-card-footer, footer')?.append(makeButton(card.dataset.storyCard || card.dataset.shareBrief));
    });
  }
  const groups=renderStoryGroups;
  renderStoryGroups=function(){groups();decorate();};
  const read=renderArticle;
  renderArticle=function(id,scroll=false){read(id,scroll);const actions=document.querySelector('.article-actions');if(actions&&!actions.querySelector('[data-share-id]')) actions.append(makeButton(id));};
  decorate();
  const dialog=document.createElement('dialog');
  dialog.className='share-dialog';
  dialog.innerHTML='<form method="dialog"><button class="share-close" aria-label="关闭分享">×</button></form><h2>分享情报</h2><p class="share-hint"></p><label for="shareText">分享内容</label><textarea id="shareText" readonly rows="5"></textarea><div class="share-controls"><button type="button" class="share-copy">复制分享内容</button></div><p class="share-status" role="status" aria-live="polite"></p>';
  document.body.append(dialog);
  dialog.querySelector('.share-copy').onclick=async()=>{
    const input=dialog.querySelector('textarea');
    try{await navigator.clipboard.writeText(input.value);dialog.querySelector('.share-status').textContent='已复制，可以粘贴到钉钉或其他应用。';}
    catch{input.focus();input.select();dialog.querySelector('.share-status').textContent='请按 ⌘C（Windows：Ctrl+C）复制已选中的内容。';}
  };
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-share-id]');if(!button)return;
    event.preventDefault();event.stopPropagation();
    const item=storyMap.get(button.dataset.shareId)||briefMap.get(button.dataset.shareId);if(!item)return;
    const local=location.protocol==='file:'||['localhost','127.0.0.1','[::1]'].includes(location.hostname);
    const article=item.story_id&&item.editorial_tier!=='brief';
    const source=(item.source_links||[]).find(s=>safeUrl(s.url)!=='#')?.url;
    const runId=String(bundle.run_id || '');
    const archived=location.pathname.includes('/archive/');
    const link=!archived && /^[A-Za-z0-9_-]+$/.test(runId)
      ? new URL('archive/'+encodeURIComponent(runId)+'/index.html',location.href)
      : new URL(location.href);
    link.hash='story='+encodeURIComponent(item.story_id||'');
    const target=article&&!local?link.href:source;
    dialog.querySelector('.share-hint').textContent=local?'本地预览分享原文链接，收件人无需访问你的电脑。':article?'复制标题、摘要和文章链接。':'复制标题、摘要和原文链接。';
    dialog.querySelector('textarea').value=[item.headline,item.dek,target].filter(Boolean).join('\n\n');
    dialog.querySelector('.share-status').textContent='';dialog.showModal();
  });
  function openShared(){
    const id=new URLSearchParams(location.hash.slice(1)).get('story');if(!id)return;
    if(storyMap.has(id))renderArticle(id,true);
    else{showView('selection');document.getElementById('channelDescription').textContent='这条分享情报已不在当前更新范围，可搜索标题或查看最新资讯。';}
  }
  window.addEventListener('hashchange',openShared);openShared();
})();
