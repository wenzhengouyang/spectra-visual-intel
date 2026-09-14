/* Same-origin account UI. Session cookies are HttpOnly; guest data stays separate. */
(() => {
  let account = null, available = false, configured = false, busy = false;
  const pending = [];
  let lastInterests = [], lastFeedback = {};
  const guest = () => { interests = readLocal(PROFILE_KEY, []); feedback = readLocal(FEEDBACK_KEY, {}); refreshBrowse(); };
  const button = document.createElement('button');
  button.className = 'share-button account-button'; button.textContent = '登录 / 注册';
  const controls=document.createElement('div');controls.className='account-controls';
  const profile=document.getElementById('openInterestProfile');profile.before(controls);controls.append(profile,button);
  const dialog = document.createElement('dialog'); dialog.className = 'account-dialog';
  dialog.innerHTML = '<form method="dialog"><button class="account-close" aria-label="关闭账号">×</button></form><h2>我的账号</h2><p class="account-description"></p><p class="account-status" role="status" aria-live="polite"></p><div class="account-actions"></div><div class="account-records"></div>';
  document.body.append(dialog);
  const status = message => { dialog.querySelector('.account-status').textContent = message; button.title = message; };
  async function request(path, payload) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...(payload ? {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':account?.csrf || ''},body:JSON.stringify(payload)} : {})});
    const result = await response.json();
    if (!response.ok) throw Object.assign(new Error(result.error || '账号服务暂不可用'), {status:response.status});
    return result;
  }
  function apply() {
    if (account?.user) { interests = [...account.data.interests]; feedback = {...account.data.feedback}; lastInterests=[...interests];lastFeedback={...feedback};refreshBrowse(); }
    button.textContent = account?.user ? account.user.name : '登录 / 注册';
    if (document.body.dataset.currentView === 'articles' && document.querySelector('[data-view-target=interests].active')) {
      document.getElementById('channelDescription').textContent = account?.user ? '关注与反馈保存在你的账号中，可跨设备同步。' : '你的关注仅保存在当前浏览器。';
    }
  }
  async function flush() {
    if (busy || !account?.user || !pending.length) return;
    busy = true; status('正在同步…');
    try {
      while (pending.length) {
        let saved = false;
        for (let attempt=0; attempt<3 && !saved; attempt++) {
          const latest = await request('/api/account');
          if (!latest.user) throw Object.assign(new Error('登录已过期，请重新登录。'), {status:401});
          account = latest;
          const data = structuredClone(account.data);
          pending[0](data);
          try { const result = await request('/api/account/data', {revision:account.revision,data}); account.data=data; account.revision=result.revision; saved=true; }
          catch(error) { if(error.status!==409 || attempt===2) throw error; }
        }
        pending.shift();
      }
      apply(); status('已同步到账号');
    } catch(error) {
      status(error.message + ' · 更改尚未同步，请保持页面打开并点击重试。');
    } finally { busy=false; render(); }
  }
  const queue = operation => { pending.push(operation); flush(); };
  function render() {
    const actions = dialog.querySelector('.account-actions'); actions.replaceChildren();
    const add = (label, fn, disabled=false) => { const b=document.createElement('button'); b.className='account-action'; b.type='button'; b.textContent=label; b.disabled=disabled; b.onclick=fn; actions.append(b); };
    dialog.querySelector('.account-description').textContent = account?.user
      ? `${account.user.name} · 普通成员。个人记录仅对你可见。`
      : available ? '登录平台账号，同步关注、收藏和阅读记录。无需钉钉，免登录也可浏览情报。' : '当前是静态浏览入口，未连接账号服务。请使用管理员提供的账号版工作台地址。';
    if (!account?.user && available) {
      const form=document.createElement('form');form.className='account-auth';
      form.innerHTML='<label>用户名<input name="username" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{2,31}" placeholder="3–32 位字母、数字或 ._-"></label><label>密码<input name="password" type="password" autocomplete="current-password" required minlength="12" maxlength="128" placeholder="至少 12 位"></label><div class="account-actions"><button class="account-action" type="submit" value="login">登录</button></div><p>请妥善保存密码。目前暂不提供邮件找回。</p>';
      if(account?.registration){const register=document.createElement('button');register.type='submit';register.value='register';register.className='account-action';register.textContent='注册新账号';form.querySelector('.account-actions').append(register);}
      form.onsubmit=async event=>{
        event.preventDefault();const mode=event.submitter?.value || 'login';
        const username=form.elements.username.value,password=form.elements.password.value;
        const buttons=form.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);status(mode==='register'?'正在创建账号…':'正在登录…');
        try{await request('/api/account/'+mode,{username,password});form.elements.password.value='';account=await request('/api/account');pending.length=0;apply();status('登录成功。可按需导入本机原有偏好。');render();}
        catch(error){status(error.message);buttons.forEach(b=>b.disabled=false);}
      };
      actions.append(form);
    }
    else if(account?.user) {
      add('导入本机关注与反馈', ()=>{
        if (!confirm('将本浏览器原有的关注与反馈合并到当前账号？账号中已有的同条反馈将保留。')) return;
        const tags=readLocal(PROFILE_KEY,[]), votes=readLocal(FEEDBACK_KEY,{});
        queue(data=>{data.interests=[...new Set([...data.interests,...(Array.isArray(tags)?tags:[])])].slice(0,30);data.feedback={...votes,...data.feedback};});
      });
      add('修改密码',()=>{
        if(pending.length||busy){status('请先完成数据同步。');return;}
        const form=document.createElement('form');form.className='account-auth';
        form.innerHTML='<label>原密码<input name="old_password" type="password" autocomplete="current-password" required maxlength="128"></label><label>新密码<input name="new_password" type="password" autocomplete="new-password" required minlength="12" maxlength="128"></label><button class="account-action" type="submit">确认修改密码</button>';
        form.onsubmit=async event=>{event.preventDefault();const b=form.querySelector('button');b.disabled=true;try{await request('/api/account/password',{old_password:form.elements.old_password.value,new_password:form.elements.new_password.value});account=await request('/api/account');status('密码已修改，其他设备的登录已失效。');render();}catch(error){status(error.message);b.disabled=false;}};
        actions.replaceChildren(form);
      });
      add('退出登录', async()=>{
        if (pending.length || busy) {status('请等待同步完成，或先重试同步。');return;}
        try {await request('/api/account/logout',{});account=await request('/api/account');guest();apply();status('已退出，当前显示本机访客偏好。');render();}
        catch(error){status(error.message);}
      });
    }
    if(pending.length) add('重试同步',flush,busy);
    if(!account?.user){
      add('导出本机偏好',()=>{
        const data={interests:readLocal(PROFILE_KEY,[]),feedback:readLocal(FEEDBACK_KEY,{})};
        const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
        const link=document.createElement('a');link.href=url;link.download='spectra-preferences.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      });
      if(!available && ['127.0.0.1','localhost'].includes(location.hostname))add('打开账号版工作台',()=>window.open('http://127.0.0.1:8012/','_blank','noopener'));
    }else{
      add('导入偏好文件',()=>{
        const input=document.createElement('input');input.type='file';input.accept='.json,application/json';
        input.onchange=async()=>{try{
          const file=input.files[0];if(!file)return;if(file.size>262144)throw Error('文件过大');
          const data=JSON.parse(await file.text());
          if(!Array.isArray(data.interests)||data.interests.length>30||data.interests.some(x=>typeof x!=='string'||!x||x.length>80)||!data.feedback||typeof data.feedback!=='object'||Array.isArray(data.feedback)||Object.keys(data.feedback).length>2000||Object.entries(data.feedback).some(([k,v])=>!k||k.length>120||!['useful','not_relevant'].includes(v)))throw Error('偏好文件格式不正确');
          if(!confirm('将文件中的关注与反馈合并到当前账号？已有同条反馈将保留。'))return;
          queue(saved=>{saved.interests=[...new Set([...saved.interests,...data.interests])].slice(0,30);saved.feedback={...data.feedback,...saved.feedback};});
        }catch(error){status(error.message);}};input.click();
      });
    }
    const records=dialog.querySelector('.account-records');records.replaceChildren();
    if(account?.user) for(const [key,label] of [['favorites','我的收藏'],['history','最近阅读']]) {
      const heading=document.createElement('h3');heading.textContent=`${label} · ${account.data[key].length}`;records.append(heading);
      for(const id of account.data[key].slice(0,30)) {
        const story=storyMap.get(id);const row=document.createElement('button');row.className='account-record';
        row.textContent=story?.headline || '历史情报（已移出本期，记录仍保留）';row.disabled=!story;
        row.onclick=()=>{dialog.close();renderArticle(id,true);};records.append(row);
      }
    }
  }
  button.onclick=()=>{render();dialog.showModal();};
  window.SpectraAccount={changed(key,value){
    if(!account?.user)return false;
    if(key===PROFILE_KEY){const previous=new Set(lastInterests);const next=new Set(value);lastInterests=[...value];queue(data=>{data.interests=[...new Set([...data.interests.filter(x=>!previous.has(x)||next.has(x)),...value])].slice(0,30);});}
    if(key===FEEDBACK_KEY){const delta=Object.fromEntries(Object.entries(value).filter(([k,v])=>lastFeedback[k]!==v));lastFeedback={...value};queue(data=>{Object.assign(data.feedback,delta);});}
    return true;
  }};
  const originalRead=renderArticle;
  renderArticle=function(id,scroll=false){
    originalRead(id,scroll);
    if(!storyMap.has(id))return;
    if(account?.user)queue(data=>{data.history=[id,...data.history.filter(x=>x!==id)].slice(0,500);});
    const save=document.createElement('button');save.className='share-button';save.textContent=account?.data?.favorites.includes(id)?'取消收藏':'收藏';
    save.onclick=()=>{if(!account?.user){button.click();return;}queue(data=>{const exists=data.favorites.includes(id);data.favorites=exists?data.favorites.filter(x=>x!==id):[id,...data.favorites].slice(0,500);save.textContent=exists?'收藏':'取消收藏';});};
    document.querySelector('.article-actions')?.append(save);
  };
  document.addEventListener('click',event=>{if(event.target.closest('[data-view-target=interests]'))apply();});
  window.addEventListener('beforeunload',event=>{if(pending.length){event.preventDefault();event.returnValue='';}});
  request('/api/account').then(result=>{available=true;configured=result.configured;account=result;apply();render();}).catch(()=>{render();});
})();
