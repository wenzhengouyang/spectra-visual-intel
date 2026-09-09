// Browser smoke checks against the local, rendered report.
const {chromium} = require(process.env.SPECTRA_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1040}});
    const errors=[];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:4174/index.html');
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
    await page.screenshot({path:'/tmp/spectra-articles-deployed.png',fullPage:true});
    await page.locator('[data-view-target=ideas]').click();
    assert.ok(await page.locator('.library-entry').count());
    await page.locator('[data-library-tab=limits]').click();
    assert.equal(await page.locator('[data-library-tab=limits]').getAttribute('aria-pressed'),'true');
    await page.locator('[data-library-read]').click();
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
