const {chromium}=require(process.env.SPECTRA_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const page=await browser.newPage();
    await page.goto('http://127.0.0.1:4174/index.html');
    await page.locator('[data-view-target=selection]').click();
    await page.locator('[data-channel-field=core_event]').click();
    assert.equal(await page.locator('.tier-industry_signal:visible').count(),0);
    assert.equal(await page.locator('[data-story-card]:visible').count(),await page.evaluate(()=>bundle.issue.core_event_count));
    const id=await page.locator('[data-story-card]').first().getAttribute('data-story-card');
    await page.locator('[data-story-card] [data-open-story]').first().click();
    await page.locator('[data-feedback=useful]').click();
    await page.locator('#backToArticles').click();
    await page.locator('[data-channel-field=p0]').click();
    await page.locator('[data-view-target=interests]').click();
    assert.equal(await page.locator(`[data-story-card="${id}"]:visible`).count(),1);
    await page.reload();
    await page.locator('[data-view-target=interests]').click();
    await page.locator(`[data-story-card="${id}"] [data-open-story]`).first().click();
    await page.locator('[data-feedback=not_relevant]').click();
    await page.locator('#backToArticles').click();
    await page.locator('[data-view-target=interests]').click();
    assert.equal(await page.locator(`[data-story-card="${id}"]:visible`).count(),0);
    await page.locator('[data-view-target=selection]').click();
    assert.equal(await page.locator(`[data-story-card="${id}"]:visible`).count(),1);
    // Route a public origin to the local candidate to exercise public share URLs.
    await page.route('https://spectra.test/**',async route=>{
      const url=new URL(route.request().url());
      const response=await page.request.get('http://127.0.0.1:4174'+url.pathname+url.search);
      await route.fulfill({response});
    });
    await page.goto('https://spectra.test/index.html');
    const runId=await page.evaluate(()=>bundle.run_id);
    await page.locator('[data-view-target=selection]').click();
    await page.locator('.story-card-footer .share-button').first().click();
    const text=await page.locator('#shareText').inputValue();
    const url=text.split('\n\n').at(-1);
    assert.ok(url.includes('/archive/'+encodeURIComponent(runId)+'/index.html#story='));
    await page.goto(url);
    assert.equal(await page.locator('body.is-reading').count(),1);
    assert.ok(await page.locator('#editorialArticle h2').textContent());
    console.log('PASS: exact core tier; feedback add/hide, reload persistence, full-list recovery; archived public share opens article.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
