/* SPECTRA visual library: source-backed reading, no invented analysis. */
window.SpectraVisualLibrary = (() => {
  let entries = [], selected = '', tab = 'method', openArticle;
  const escape = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = value => typeof value === 'string' ? value : value?.text || '';
  const url = value => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
  function render() {
    const root = document.getElementById('visualLibrary');
    const story = entries.find(item => item.story_id === selected) || entries[0];
    if (!story) { root.innerHTML = '<div class="library-empty">本期暂无适合展开的视觉资讯。新的正式文章出现后，这里会展示其中的方法、边界与来源。</div>'; return; }
    selected = story.story_id;
    const method = text(story.under_the_hood) || text(story.what_happened) || story.dek;
    const insight = text(story.our_take) || text(story.why_it_matters);
    const limits = Array.isArray(story.limitations) ? story.limitations.map(text).filter(Boolean).join('；') : text(story.limitations);
    const sources = (story.source_links || []).filter(source => url(source.url));
    const sourceLinks = sources.map(source => `<a href="${escape(url(source.url))}" target="_blank" rel="noopener noreferrer">${escape(source.label || '原文')} ↗</a>`).join('');
    const content = tab === 'source'
      ? `<section><small>关联情报</small><h3>${escape(story.headline)}</h3><p>${escape(story.dek)}</p></section><div class="library-sources">${sourceLinks}</div>`
      : tab === 'limits'
        ? `<section><h3>证据与能力边界</h3><p>${escape(limits || '当前文章未提供明确的能力边界，不据此推断模型的通用表现。请结合原文实验条件阅读。')}</p></section><section class="library-note"><h3>继续观察</h3><p>${escape(text(story.watch_next) || '暂未收录后续验证，不能将研究展示直接视为产品能力。')}</p></section>`
        : `<section><h3>新闻里的做法</h3><p>${escape(method)}</p></section>${insight ? `<section class="library-note"><h3>值得留意</h3><p>${escape(insight)}</p><small>编辑判断 · 沿用关联文章，不代表团队已验证</small></section>` : ''}`;
    root.innerHTML = `<div class="library-layout"><aside class="library-list" aria-label="选择资讯观察">${entries.map(item => `<button class="library-entry" data-library-id="${escape(item.story_id)}" aria-pressed="${selected === item.story_id}"><small>${escape(item.category)}<span>→</span></small><strong>${escape(item.headline)}</strong><small>${escape((item.published_at || '').slice(0,10))} · 来自正式情报</small></button>`).join('')}</aside><article class="library-detail"><small>${escape(story.category)}</small><h2>${escape(story.headline)}</h2><p class="library-summary">${escape(story.one_line_takeaway || story.dek)}</p><div class="library-tabs" aria-label="阅读内容">${[['method','值得留意的做法'],['limits','能力边界'],['source','来源资讯']].map(([key,label]) => `<button data-library-tab="${key}" aria-pressed="${tab === key}">${label}</button>`).join('')}</div><div class="library-reading" aria-live="polite">${content}</div><div class="library-actions"><button data-library-read="${escape(story.story_id)}">阅读关联情报 →</button>${sourceLinks}</div></article></div>`;
  }
  return {
    mount(bundle, reader) {
      openArticle = reader;
      entries = (bundle.editorial_stories || []).filter(story => story.domain_scope !== 'scope.ai_extended' && story.editorial_tier !== 'brief' && /视频|图像|世界模型|空间|4D|评测|视觉|具身/.test(story.category || ''));
      document.getElementById('ideasStatus').textContent = `${entries.length} 条资讯观察`;
      const root = document.getElementById('visualLibrary');
      root.onclick = event => { const button = event.target.closest('button'); if (!button) return;
        if (button.dataset.libraryId) { selected = button.dataset.libraryId; tab = 'method'; render(); }
        if (button.dataset.libraryTab) { tab = button.dataset.libraryTab; render(); }
        if (button.dataset.libraryRead) openArticle(button.dataset.libraryRead, true);
      };
      render();
    },
    open(storyId) { if (!entries.some(item => item.story_id === storyId)) return false; selected = storyId; tab = 'method'; render(); return true; },
    includes(storyId) { return entries.some(item => item.story_id === storyId); }
  };
})();
