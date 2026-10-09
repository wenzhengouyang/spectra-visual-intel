const {chromium}=require(process.env.SPECTRA_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const user='qa_'+Date.now(), password='test-only-password-42';
 const errors=[];
 async function auth(page,register=false,name=user){
  await page.goto('http://127.0.0.1:8011/');
  await page.locator('.account-button').click();
  await page.locator('[name=username]').fill(name);await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:register?'注册新账号':'登录',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.account-status').textContent.includes('登录成功'));
  await page.locator('.account-dialog .account-close').click();
 }
 try{
  const context=await browser.newContext(),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{localStorage.setItem('spectra_interest_profile_v2',JSON.stringify(['世界模型']));});
  await auth(page,true);
  assert.equal(await page.evaluate(()=>interests.length),0,'guest preferences must not auto-import');
  await page.locator('.account-button').click();page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'导入本机关注与反馈'}).click();
  await page.waitForFunction(()=>document.querySelector('.account-status').textContent==='已同步到账号');
  await page.locator('.account-dialog .account-close').click();
  await page.locator('[data-view-target=selection]').click();const id=await page.locator('[data-story-card]').first().getAttribute('data-story-card');
  await page.locator('[data-story-card] [data-open-story]').first().click();
  await page.getByRole('button',{name:'收藏',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.account-status').textContent==='已同步到账号');
  const second=await browser.newContext(),page2=await second.newPage();await auth(page2);
  assert.ok((await page2.evaluate(()=>interests)).includes('世界模型'));
  await page2.locator('.account-button').click();
  assert.ok((await page2.locator('.account-records').textContent()).includes('我的收藏 · 1'));
  assert.ok((await page2.locator('.account-records').textContent()).includes('最近阅读 · 1'));
  await page2.getByRole('button',{name:'退出登录',exact:true}).click();
  await page2.waitForFunction(()=>document.querySelector('.account-button').textContent==='登录 / 注册');
  await page2.locator('.account-dialog .account-close').click();
  await auth(page2,true,user+'_other');assert.equal(await page2.evaluate(()=>interests.length),0);
  await page2.locator('.account-button').click();assert.ok((await page2.locator('.account-records').textContent()).includes('我的收藏 · 0'));
  await page2.setViewportSize({width:390,height:844});await page2.screenshot({path:'/tmp/spectra-account-mobile.png'});
  assert.ok(await page2.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);console.log('PASS: registration, login on second browser, explicit guest import, favorites/history sync, logout, user isolation, mobile layout.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
