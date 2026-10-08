(() => {
  const anchor = document.querySelector('.overview-main');
  if (!anchor || document.getElementById('model-universe-section')) return;
  const section = document.createElement('section');
  section.id = 'model-universe-section';
  section.dataset.platformView = 'overview';
  const frame = document.createElement('iframe');
  frame.title = '模型宇宙：方向、公司与模型档案';
  frame.src = 'assets/model-universe/index.html';
  frame.style.cssText = 'display:block;width:100%;height:800px;border:0;background:#09131e;border-radius:12px';
  section.style.cssText = 'margin:24px 0';
  section.append(frame);
  anchor.before(section);
})();
