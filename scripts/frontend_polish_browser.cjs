/* Production renderer, mock models and isolated temporary profiles only. */
const fs=require('fs'),path=require('path');
module.exports=async function(browser,base='http://127.0.0.1:8577/'){
  if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(base))throw Error('Offline localhost only');
  const out=path.resolve('artifacts/frontend-polish');fs.mkdirSync(out,{recursive:true});
  const results=[];let page;
  const record=(name,value=true)=>{results.push({name,value});fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(results,null,2));};
  const assert=(value,message)=>{if(!value)throw Error(message);};
  const button=name=>page.getByRole('button',{name,exact:true});
  const idle=async()=>{await page.waitForTimeout(400);await page.locator('[data-testid=stStatusWidget]').waitFor({state:'hidden',timeout:30000});await page.waitForTimeout(200);};
  const side=()=>page.locator('[data-testid=stSidebar]');
  const openSide=async()=>{if(await side().getAttribute('aria-expanded')==='false')await page.locator('[data-testid=collapsedControl]').click();};
  const closeSide=async()=>{if(page.viewportSize().width<800&&await side().getAttribute('aria-expanded')==='true')await page.locator('[data-testid=stSidebarContent]>div:first-child button').click();await page.waitForTimeout(350);};
  const nav=async(name)=>{await openSide();await button(name).click();await page.waitForFunction(name=>document.querySelector('.cf-workspace-header')?.innerText.includes(name),name);await idle();await closeSide();};
  const shot=async(name)=>{await page.waitForTimeout(350);await page.evaluate(()=>{document.querySelector('section.main').scrollTop=0;const d=document.querySelector('[data-campusflow-dialog]');if(d)d.scrollTop=0;});await page.screenshot({path:path.join(out,name+'.png')});};
  const open=async(width=1440)=>{const context=await browser.newContext({viewport:{width,height:width===1440?900:844}});context.setDefaultTimeout(6000);page=await context.newPage();await page.goto(base+'?identity=anonymous&ui_probe=1&profile=visual&documents=1&estimate_rehearsal=workload');await button('任务估时').waitFor({timeout:20000});await idle();await closeSide();};
  const probe=async()=>{const exp=page.locator('details').filter({has:page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true})});await exp.locator('summary').first().click();const value=JSON.parse((await page.locator('pre').allTextContents()).find(t=>t.includes('"material_attached"')));await exp.locator('summary').first().click();return value;};
  const noOverflow=async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.querySelector('section.main').scrollWidth<=document.querySelector('section.main').clientWidth+1),'Horizontal overflow');
  const field=label=>page.locator('[data-testid=stSelectbox]').filter({hasText:label}).getByRole('combobox');
  const environment=async()=>{
    assert(await side().getByRole('button',{name:'校区与时间',exact:true}).count()===0,'Fake sidebar page remains');
    await button('切换校区').click();await idle();assert(await field('当前校区').isVisible()&&!await field('小时').isVisible(),'Campus entry did not open independently');
    await button('时间设置').click();await idle();assert(await field('小时').isVisible(),'Time entry requires a second disclosure');
  };
  const align=async()=>{
    const a=await button('补充我的情况').boundingBox(),b=await button('帮我看看').boundingBox(),c=await page.getByRole('textbox',{name:'任务、通知或说明',exact:true}).boundingBox();
    assert(Math.abs(a.x-b.x)<=1&&Math.abs(c.x-b.x)<=1,'Action left edges differ');return {quietX:a.x,primaryX:b.x,inputX:c.x,gap:b.y-a.y-a.height};
  };
  try{
    await open();await environment();
    const geometry=await page.evaluate(()=>{
      const widget=text=>[...document.querySelectorAll('[data-testid=stSelectbox]')].find(e=>e.querySelector('label')?.innerText===text);
      const metrics=text=>{const e=widget(text).querySelector('[data-baseweb=select]>div'),r=e.getBoundingClientRect(),s=getComputedStyle(e);return{x:r.x,y:r.y,width:r.width,height:r.height,font:s.fontSize,color:s.color,border:s.border,background:s.backgroundColor};};
      const titles=[...document.querySelectorAll('.cf-campus-kicker')].map(e=>({y:e.getBoundingClientRect().y,font:getComputedStyle(e).fontSize,color:getComputedStyle(e).color}));
      const summary=document.querySelector('.cf-environment-summary').getBoundingClientRect();return{campus:metrics('当前校区'),hour:metrics('小时'),minute:metrics('分钟'),titles,summaryGap:titles[0].y-summary.bottom};
    });
    assert(geometry.summaryGap>=24,'Environment controls too close to summary');
    assert(Math.abs(geometry.campus.y-geometry.hour.y)<1&&Math.abs(geometry.hour.y-geometry.minute.y)<1,'Environment baselines differ');
    for(const k of ['height','font','color','border','background'])assert(geometry.campus[k]===geometry.hour[k]&&geometry.hour[k]===geometry.minute[k],'Environment styles differ: '+k);
    assert(Math.abs(geometry.campus.width-(geometry.minute.x+geometry.minute.width-geometry.hour.x))<2,'Campus/time group widths differ');
    await shot('01-today-environment-1440');record('independent entries, matched controls and environment spacing',geometry);
    await field('小时').fill('15');await page.getByRole('option',{name:'15',exact:true}).click();await idle();
    assert((await page.locator('.cf-environment-summary').innerText()).includes('15:00'),'Manual clock lost');
    await button('使用当前时间').click();await idle();assert((await page.locator('.cf-environment-summary').innerText()).includes('14:00'),'System clock reset lost');
    assert((await probe()).calls===0,'Environment called model');
    await nav('任务估时');assert(!await field('当前校区').isVisible()&&!await field('小时').isVisible(),'Navigation left controls open');
    assert(await page.locator('h1.cf-estimator-title').count()===1,'Missing/duplicate tool title');
    assert(!(await page.locator('body').innerText()).includes('材料估时'),'Old visible view name');
    const title=await page.locator('h1.cf-estimator-title').boundingBox(),upload=await page.locator('[data-testid=stFileUploader]>label').boundingBox();
    assert(upload.y-title.y-title.height<40,'Large gap below task title');
    record('compact task tool and aligned operations',await align());await shot('02-task-estimation-empty-1440');
    await button('补充我的情况').click();await idle();await page.getByRole('textbox',{name:'补充说明',exact:true}).fill('填表');await page.getByRole('textbox',{name:'补充说明',exact:true}).press('Tab');await idle();
    await align();await shot('03-task-estimation-expanded-1440');
    await button('个人设置').click();await idle();const d=page.getByRole('dialog');assert(Math.abs((await d.boundingBox()).width-1440*.48)<2,'Settings width changed');
    await page.getByRole('tab',{name:'我的偏好',exact:true}).click();await shot('04-settings-1440');
    await page.getByRole('tab',{name:'常用地点',exact:true}).click();await page.getByRole('tab',{name:'我的课表',exact:true}).click();
    await button('关闭个人设置').click();await d.waitFor({state:'detached'});await idle();
    assert(await page.getByRole('textbox',{name:'补充说明',exact:true}).inputValue()==='填表','Settings lost supplement');
    await page.locator('input[type=file]').setInputFiles(path.resolve('artifacts/document_material/workload/assessment.docx'));
    await page.getByText(/已读取材料/).waitFor({state:'attached',timeout:15000});await idle();const attached=await probe();
    await environment();await button('使用当前时间').click();await idle();await nav('今天');await nav('任务估时');
    const retained=await probe();assert(retained.material_attached&&retained.material_bytes===attached.material_bytes&&retained.supplement==='填表'&&retained.calls===attached.calls,'Environment/navigation lost upload or intent');
    await page.evaluate(()=>{window.polishFrames=[];window.polishWatch=setInterval(()=>{const visible=e=>!!e.getClientRects().length;window.polishFrames.push({supplements:[...document.querySelectorAll('textarea[aria-label="补充说明"]')].filter(visible).length,submits:[...document.querySelectorAll('button')].filter(visible).filter(e=>/^(帮我看看|重新估算)$/.test(e.innerText.trim())).length,thinking:document.body.innerText.includes('THINKING.......')});},30);});
    await button('帮我看看').click();await page.locator('.cf-material-estimate').waitFor({timeout:30000});await idle();
    const frames=await page.evaluate(()=>{clearInterval(window.polishWatch);return window.polishFrames;});assert(frames.some(f=>f.thinking)&&frames.every(f=>f.supplements<=1&&f.submits<=1),'Thinking missing or duplicated');
    assert((await page.locator('.cf-material-estimate').innerText()).includes('35–65')&&(await probe()).plan===null,'Estimate or confirmation gate changed');
    await shot('check-task-result-1440');record('settings, upload, supplement, thinking and confirmation gate',{frames:frames.length,maxSupplement:Math.max(...frames.map(f=>f.supplements)),maxSubmit:Math.max(...frames.map(f=>f.submits))});
    await page.context().close();await open(390);await environment();await noOverflow();await shot('check-environment-390');
    await nav('任务估时');await noOverflow();await align();await shot('check-task-empty-390');
    await button('补充我的情况').click();await idle();await noOverflow();await shot('check-task-expanded-390');
    await openSide();await button('个人设置').click();await idle();await noOverflow();const close=await button('关闭个人设置').boundingBox();assert(close.width>=43&&close.x+close.width<=390,'Settings close inaccessible');await shot('check-settings-390');
    await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'detached'});record('390 environment, task input/expanded and settings; no overflow');
    await page.context().close();record('complete');return{out,results};
  }catch(e){if(page&&!page.isClosed()){await page.screenshot({path:path.join(out,'failure.png')});record('failure text',(await page.locator('body').innerText()).slice(-5000));}record('failure',String(e));throw e;}
};
