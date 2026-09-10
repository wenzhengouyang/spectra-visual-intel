// Browser smoke checks against the local, rendered report.
const {chromium} = require(process.env.SPECTRA_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1040}});
    const errors=[];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.SPECTRA_UI_URL || 'http://127.0.0.1:4174/index.html');
    for (const width of [1920,1440,1280]) {
      await page.setViewportSize({width,height:1080});
      for (const view of ['overview','selection','interests','ideas']) {
        await page.locator(`[data-view-target=${view}]`).click();
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Desktop overflow: ${width}/${view}`);
      }
      await page.locator('[data-view-target=selection]').click();
      const geometry = await page.evaluate(()=>{
        const box=q=>document.querySelector(q).getBoundingClientRect();
        return {headerBottom:box('.workspace-header').bottom,tabsTop:box('.channel-field-bar').top,tabsLeft:box('.channel-field-bar').left,cardsLeft:box('.story-card-grid').left};
      });
      assert.ok(geometry.tabsTop>=geometry.headerBottom+20,'Tabs overlap the header');
      assert.ok(Math.abs(geometry.tabsLeft-geometry.cardsLeft)<2,'Separator and cards must share a left edge');
      await page.locator('#advancedFilterToggle').click();
      assert.ok(await page.locator('#advancedFilterPanel').isVisible());
      await page.locator('#advancedFilterToggle').click();
      await page.locator('.story-card-image img').evaluateAll(images=>Promise.all(images.map(img=>img.decode().catch(()=>{}))));
      await page.screenshot({path:`/tmp/spectra-desktop-${width}.png`});
    }
    await page.setViewportSize({width:1440,height:1040});
    await page.locator('[data-view-target=overview]').click();
    await page.screenshot({path:'/tmp/spectra-overview-deployed.png',fullPage:true});
    assert.equal(await page.locator('.timeline-event-card img').count(),0);
    await page.locator('.trend-point').first().click();
    assert.ok(await page.locator('.related-day').count());
    const date = await page.locator('.day-node.selected').getAttribute('data-date');
    await page.locator('.timeline-event-card [data-open-story]').first().click();
    await page.locator('#backToArticles').click();
    assert.equal(await page.locator('body').getAttribute('data-current-view'),'overview');
    assert.equal(await page.locator('.day-node.selected').getAttribute('data-date'),date);
    await page.locator('[data-view-target=selection]').click();
    assert.equal(await page.locator('.story-card-grid').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),3);
    assert.equal(await page.locator('[data-story-card], [data-share-brief]').count(),await page.locator('.editorial-browse [data-share-id]').count());
    await page.context().grantPermissions(['clipboard-read','clipboard-write']);
    await page.locator('.story-card-footer .share-button').first().click();
    assert.ok(await page.locator('.share-dialog').isVisible());
    const shareText=await page.locator('#shareText').inputValue();
    assert.ok(shareText.includes('https://')&&!shareText.includes('127.0.0.1'));
    await page.locator('.share-copy').click();
    await page.waitForFunction(()=>document.querySelector('.share-status').textContent.includes('已复制'));
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),shareText);
    await page.locator('.share-close').click();
    await page.locator('.news-brief-card .share-button').first().click();
    assert.ok((await page.locator('#shareText').inputValue()).includes('https://'));
    await page.locator('.share-close').click();
    await page.setViewportSize({width:820,height:1040});
    assert.equal(await page.locator('.story-card-grid').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),3);
    await page.setViewportSize({width:1440,height:1040});
    await page.screenshot({path:'/tmp/spectra-articles-deployed.png',fullPage:true});
    await page.locator('[data-view-target=ideas]').click();
    assert.ok(await page.locator('.library-entry').count());
    await page.locator('[data-library-tab=limits]').click();
    assert.equal(await page.locator('[data-library-tab=limits]').getAttribute('aria-pressed'),'true');
    await page.locator('[data-library-read]').click();
    for (const width of [1440,390]) {
      await page.setViewportSize({width,height:900});
      await page.locator('.article-section').first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
      const heading = await page.locator('.reader-heading').boundingBox();
      assert.ok(Math.abs(heading.y) < 2, 'Reader navigation must stay at viewport top');
      assert.equal(await page.locator('.article-section>div>small:visible').count(),0);
      assert.equal(await page.locator('.article-section').first().evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(26, 46, 64)');
      await page.screenshot({path:`/tmp/spectra-reader-${width}.png`});
    }
    await page.setViewportSize({width:1440,height:1040});
    await page.locator('#backToArticles').click();
    assert.equal(await page.locator('body').getAttribute('data-current-view'),'ideas');
    await page.screenshot({path:'/tmp/spectra-library-deployed.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    for(const view of ['overview','selection','ideas']){
      await page.locator(`[data-view-target=${view}]`).click();
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),`overflow in ${view}`);
    }
    await page.locator('[data-view-target=overview]').click();
    await page.screenshot({path:'/tmp/spectra-mobile-deployed.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('PASS: desktop/mobile, three-column articles, image-free timeline, radar links, reading return, visual library; no JS errors.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
