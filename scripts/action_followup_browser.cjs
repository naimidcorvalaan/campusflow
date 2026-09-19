/* Controlled-delay acceptance through the real production page, localhost only. */
const fs=require('fs'),path=require('path');
module.exports=async function(browser, base='http://127.0.0.1:8581/') {
  if(!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(base))throw Error('Local fake only');
  const out=path.resolve('artifacts/action-followup');fs.mkdirSync(out,{recursive:true});
  let p;const results=[];const check=(ok,message)=>{if(!ok)throw Error(message);};
  const record=(name,data)=>{results.push({name,data});fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(results,null,2));};
  const button=name=>p.getByRole('button',{name,exact:true});
  const idle=async()=>{await p.waitForTimeout(400);await p.locator('[data-testid=stStatusWidget]').waitFor({state:'hidden',timeout:60000});await p.waitForTimeout(250);};
  const shot=async name=>{await p.evaluate(()=>document.querySelector('section.main').scrollTop=0);await p.screenshot({path:path.join(out,name+'.png')});};
  const probe=async()=>{const d=p.locator('details').filter({has:p.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true})});if(!await d.evaluate(e=>e.open))await d.locator('summary').click();const raw=(await p.locator('pre').allTextContents()).find(t=>t.includes('"material_attached"'));await d.locator('summary').click();return JSON.parse(raw);};
  const sample=()=>p.evaluate(()=>{
    const visible=e=>e.checkVisibility({checkVisibilityCSS:true})&&!e.closest('details:not([open])');
    return {plans:document.querySelectorAll('textarea[aria-label="接下来想做什么？"]').length,
      materialVisible:[...document.querySelectorAll('textarea[aria-label="或用文字描述你的任务"]')].some(visible),
      thinking:!!document.querySelector('[data-testid=stStatusWidget]'),
      planVisible:[...document.querySelectorAll('textarea[aria-label="接下来想做什么？"]')].filter(visible).length};
  });
  try {
    p=await browser.newPage({viewport:{width:1440,height:1000}});
    await p.goto(base+'?identity=anonymous&ui_probe=1&ui_delay=1&confirmation=end');
    await button('任务估时').click();await idle();await p.getByRole('textbox',{name:'或用文字描述你的任务',exact:true}).fill('材料草稿应保留');
    await button('制定计划').click();await idle();
    const intake=p.getByRole('textbox',{name:'接下来想做什么？',exact:true});
    const story='我现在在图书馆，写90分钟作业，然后背60分钟单词。19:00去9教上课，上课前吃晚饭。';
    await intake.fill(story);await shot('empty-desktop');await button('帮我安排').click({noWaitAfter:true});
    const samples=[];
    for(let i=0;i<24;i++){await p.waitForTimeout(250);samples.push(await sample());if([2,8,18].includes(i))await shot('waiting-'+i);}
    record('initial waiting samples',samples);
    check(samples.every(s=>s.plans===1&&!s.materialVisible),'Duplicate intake or residual material during waiting');
    check(samples.some(s=>s.thinking),'Delay never observed');
    await p.getByRole('textbox',{name:'你的回答',exact:true}).waitFor({timeout:60000});await idle();
    const before=await probe();check(before.roles.filter(r=>r==='extract').length===1,'Duplicate initial submit');
    await shot('confirmation-desktop');
    const answer=p.getByRole('textbox',{name:'你的回答',exact:true});
    await answer.fill('还不确定');await button('确认并继续').click();await idle();
    check(await answer.inputValue()==='还不确定','Rejected answer lost');check((await probe()).plan===before.plan,'Rejected answer published');
    await shot('confirmation-rejected');
    await answer.fill('21:00');await button('确认并继续').click();await idle();
    const after=await probe();check(after.commitments[0].end.endsWith('21:00:00'),'Answer not applied to class end');
    check(after.commitments[0].start===before.commitments[0].start&&after.tasks===before.tasks,'Unrelated facts changed');
    check(await p.getByRole('textbox',{name:'你的回答',exact:true}).count()===0,'Answer widget remains after resolution');
    check(await p.getByText('查看地图与路线详情',{exact:true}).count()===0&&await button('按当前信息重新安排').count()===0,'Removed entries remain');
    check(await p.locator('.cf-mini-route').count()===0,'Map still mounted');
    record('bound confirmation', {before:before.commitments,after:after.commitments,bindings:after.bindings,roles:after.roles});
    await shot('normal-desktop');
    const summary=await p.locator('.cf-plan-summary p').evaluate(e=>({font:getComputedStyle(e).fontSize,line:getComputedStyle(e).lineHeight}));record('summary style',summary);
    check(parseFloat(summary.font)>=16&&parseFloat(summary.line)>=25,'Summary too small');
    await button('重新填写今日安排').click();await idle();check(await intake.inputValue()===story,'Intake draft lost');
    check((await probe()).calls===after.calls,'Opening rewrite called model');await button('重新填写今日安排').click();await idle();
    await button('重新填写今日安排').click();await idle();await intake.fill(story+'请保留这些安排。');
    await button('帮我安排').click({noWaitAfter:true});
    const rewrite=[];for(let i=0;i<18;i++){await p.waitForTimeout(200);rewrite.push(await sample());}
    check(rewrite.every(s=>s.plans===1&&!s.materialVisible),'Rewrite left stale form');record('rewrite waiting samples',rewrite);
    await idle();await p.getByRole('textbox',{name:'你的回答',exact:true}).fill('21:00');await button('确认并继续').click();await idle();
    await p.setViewportSize({width:1920,height:1080});await shot('normal-wide');
    await p.setViewportSize({width:390,height:844});
    const side=p.locator('[data-testid=stSidebar]');if(await side.getAttribute('aria-expanded')==='true')await p.locator('[data-testid=stSidebarContent]>div:first-child button').click();
    await p.waitForTimeout(400);await shot('normal-mobile');
    check(await p.evaluate(()=>document.querySelector('section.main').scrollWidth<=document.querySelector('section.main').clientWidth+1),'Mobile overflow');
    check(await p.locator('.cf-focus-next').count()===0,'Hero repeats next action');
    await p.setViewportSize({width:1440,height:1000});if(await side.getAttribute('aria-expanded')==='false')await p.locator('[data-testid=collapsedControl]').click();
    await button('任务估时').click();await idle();check(await p.getByRole('textbox',{name:'或用文字描述你的任务',exact:true}).inputValue()==='材料草稿应保留','Material draft lost');
    await p.close();
    p=await browser.newPage({viewport:{width:1440,height:1000}});
    await p.goto(base+'?identity=anonymous&ui_probe=1&ui_delay=1&confirmation=end');await idle();
    const dev=p.locator('details').filter({has:p.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true})});
    const mode=async value=>{await idle();if(!await dev.evaluate(e=>e.open))await dev.locator('summary').click();await p.waitForTimeout(400);await p.locator('[data-testid=stSelectbox]').filter({hasText:'演示服务状态'}).getByRole('combobox').click();await p.getByRole('option',{name:value,exact:true}).click();await idle();if(await dev.evaluate(e=>e.open))await dev.locator('summary').click();};
    await mode('模拟网络失败');const retryInput=p.getByRole('textbox',{name:'接下来想做什么？',exact:true});await retryInput.fill(story);await button('帮我安排').click();await idle();
    check(await retryInput.inputValue()===story,'Failed intake cleared text');check((await probe()).plan===null,'Failed intake published');check((await sample()).plans===1,'Failed intake duplicated');await shot('initial-failure-retains-input');
    await mode('正常');await button('帮我安排').click();const retry=[];
    for(let i=0;i<18;i++){await p.waitForTimeout(200);retry.push(await sample());}
    check(retry.every(s=>s.plans===1&&!s.materialVisible),'Retry duplicated or showed estimator');record('retry waiting samples',retry);
    await idle();await p.getByRole('textbox',{name:'你的回答',exact:true}).waitFor();await p.close();
    for(const answerValue of ['九点','21:00']) {
      p=await browser.newPage({viewport:{width:390,height:844}});await p.goto(base+'?identity=anonymous&ui_probe=1&confirmation='+(answerValue==='21:00'?'next':'end'));
      await p.getByRole('textbox',{name:'接下来想做什么？',exact:true}).fill(story);await button('帮我安排').click();await idle();
      await shot(answerValue==='九点'?'confirmation-mobile':'next-question-before');
      await p.getByRole('textbox',{name:'你的回答',exact:true}).fill(answerValue);await button('确认并继续').click();await idle();
      const state=await probe();check(state.commitments[0].end.endsWith('21:00:00'),'Natural time answer failed');
      if(answerValue==='21:00'){check((await p.locator('.cf-plan-provisional').innerText()).includes('休息'),'Wrong next question');await shot('next-question-mobile');}
      record('answer '+answerValue,state.commitments);await p.close();
    }
    record('complete',true);return results;
  }catch(e){if(p&&!p.isClosed()){await p.screenshot({path:path.join(out,'failure.png')});record('failure',String(e));record('visible text',(await p.locator('body').innerText()).slice(-4000));}throw e;}
};
