/* Approved overview UX: optical grating, source-linked trends and reading return. */
(() => {
  if (typeof bundle === 'undefined' || !bundle) return;
  const ideas = document.getElementById('ideas');
  ideas.innerHTML = '<div class="ideas-heading"><p>从真实资讯中，留意方法、边界与后续进展。</p><span class="ideas-status" id="ideasStatus"></span></div><div id="visualLibrary"></div>';
  window.SpectraVisualLibrary.mount(bundle, (id) => renderArticle(id, true));
  let readingOrigin = null;
  const originalShowView = showView;
  showView = function(target, scroll = true) {
    originalShowView(target, scroll);
    document.body.classList.remove('is-reading');
  };
  const originalRead = renderArticle;
  renderArticle = function(id, scroll = false) {
    if (!document.body.classList.contains('is-reading')) {
      readingOrigin = {view: document.querySelector('.nav-item.active')?.dataset.viewTarget || 'selection', y: window.scrollY};
    }
    originalRead(id, scroll);
    document.getElementById('backToArticles').textContent = readingOrigin?.view === 'overview' ? '← 返回情报总览' : readingOrigin?.view === 'ideas' ? '← 返回视觉灵感库' : '← 返回文章列表';
    if (window.SpectraVisualLibrary.includes(id)) {
      const button = document.createElement('button');
      button.className = 'library-jump';
      button.textContent = '查看相关方法与边界 →';
      button.onclick = () => { window.SpectraVisualLibrary.open(id); showView('ideas'); };
      document.querySelector('.article-actions').prepend(button);
    }
  };
  document.getElementById('backToArticles').addEventListener('click', event => {
    event.stopImmediatePropagation();
    const origin = readingOrigin || {view:'selection', y:0};
    showView(origin.view, false);
    window.scrollTo({top:origin.y, behavior:'instant'});
    readingOrigin = null;
  }, true);

  const days = bundle.presentation.timeline_days;
  const dayNames = {MON:'周一',TUE:'周二',WED:'周三',THU:'周四',FRI:'周五',SAT:'周六',SUN:'周日'};
  document.querySelectorAll('.day-node').forEach(node => {
    const label = node.querySelector('.day-name');
    label.textContent = dayNames[label.textContent] || label.textContent;
    node.setAttribute('aria-label', `${label.textContent} ${node.dataset.date}`);
  });
  let relatedIds = new Set();
  function highlightEvents() {
    document.querySelectorAll('.timeline-event-card').forEach(card => {
      const id = card.querySelector('[data-open-story]')?.dataset.openStory;
      card.classList.toggle('related-event', relatedIds.has(id));
    });
    document.querySelectorAll('.day-node').forEach(node => node.setAttribute('aria-pressed', String(node.classList.contains('selected'))));
  }
  const originalDay = renderTimelineDay;
  renderTimelineDay = function(day) { originalDay(day); highlightEvents(); };
  const trends = bundle.presentation.trend_radar;
  document.querySelectorAll('.trend-point').forEach((point, index) => {
    const label = document.createElement('span');
    label.textContent = trends[index].label;
    point.append(label);
  });
  function arrangeLabels() {
    const plot = document.getElementById('radarPlot').getBoundingClientRect();
    const occupied = [];
    document.querySelectorAll('.trend-point').forEach(point => {
      const label = point.querySelector('span');
      const dot = point.getBoundingClientRect();
      const width = label.getBoundingClientRect().width, height = label.getBoundingClientRect().height;
      for (const [dx,dy] of [[12,-height-6],[-width-6,24],[12,28],[-width-6,-height-8],[12,-height-32],[12,54]]) {
        const x = Math.max(plot.left+4, Math.min(plot.right-width-4, dot.left+dx));
        const y = Math.max(plot.top+4, Math.min(plot.bottom-height-4, dot.top+dy));
        const box = {x,y,right:x+width,bottom:y+height};
        if (occupied.some(b => x < b.right+5 && box.right > b.x-5 && y < b.bottom+5 && box.bottom > b.y-5)) continue;
        label.style.left = `${x-dot.left}px`; label.style.top = `${y-dot.top}px`; label.style.bottom = 'auto';
        occupied.push(box); break;
      }
    });
  }
  requestAnimationFrame(arrangeLabels);
  new ResizeObserver(arrangeLabels).observe(document.getElementById('radarPlot'));
  document.getElementById('trend-radar').addEventListener('click', event => {
    const target = event.target.closest('[data-trend-index], [data-trend-key]');
    if (!target) return;
    const trend = trends[Number(target.dataset.trendIndex ?? target.dataset.trendKey)];
    const eventIds = new Set(trend.event_ids || []);
    relatedIds = new Set(bundle.editorial_stories.filter(story =>
      (story.related_event_ids || [story.primary_event_id]).some(id => eventIds.has(id))
    ).map(story => story.story_id));
    document.querySelectorAll('.day-node').forEach(node => {
      const day = days.find(day => day.date === node.dataset.date);
      node.classList.toggle('related-day', day.story_ids.some(id => relatedIds.has(id)));
    });
    const selected = document.querySelector('.day-node.selected')?.dataset.date;
    const relatedDays = days.filter(day => day.story_ids.some(id => relatedIds.has(id)));
    const day = relatedDays.find(day => day.date === selected) || relatedDays.at(-1);
    if (day) renderTimelineDay(day);
    document.getElementById('radarLinkStatus').textContent = day ? `${trend.label} · 已标记 ${relatedDays.length} 天关联事件` : `${trend.label} · 当前时间轴暂无关联事件`;
  });
  const status = document.createElement('p');
  status.id = 'radarLinkStatus'; status.className = 'radar-link-status'; status.setAttribute('aria-live','polite');
  status.textContent = '选择趋势，查看右侧关联事件 →';
  document.getElementById('trend-radar').append(status);
  highlightEvents();
})();
