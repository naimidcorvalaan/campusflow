/* Real Streamlit controls, finite synthetic responses, local rehearsal only. */
const {chromium}=require('playwright-core');
const fs=require('fs'),path=require('path');
const base=process.env.CAMPUSFLOW_PREVIEW_URL||'http://127.0.0.1:8542';
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base))throw Error('Local preview only');
const screenshots=path.resolve('screenshots'), evidence=path.resolve('artifacts/release_preview');
const assert=(x,m)=>{if(!x)throw Error(m);};
const story='在图书馆写90分钟作业，然后背60分钟单词，可以拆开。19:00去9教上课，上一小时半，上课前吃晚饭。';
const notice='@所有人 第三章作业周五之前交哈，3-12，8题过程写全，还是学习通那个入口，别忘了';
const estimateEntry='难以估计任务时间？让campusflow帮你估';
const syntheticImage=process.env.CAMPUSFLOW_SYNTHETIC_IMAGE;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CAMPUSFLOW_BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 fs.mkdirSync(screenshots,{recursive:true});fs.mkdirSync(evidence,{recursive:true});
 const records=[];
 const button=n=>page.getByRole('button',{name:n,exact:true});
 const input=n=>page.getByRole('textbox',{name:n,exact:true});
 const wait=()=>page.waitForTimeout(900);
 async function fill(name,text){await input(name).fill(text);await input(name).press('Tab');await wait();}
 async function align(locator){await locator.evaluate(e=>{const scroll=e.closest('[role="dialog"]')||document.querySelector('section.main');scroll.scrollTop+=e.getBoundingClientRect().top-scroll.getBoundingClientRect().top-65;});await wait();}
 async function check(name,target=page){
   const m=await target.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
      cards:[...document.querySelectorAll('.cf-plan-grid .cf-timeline-card,.cf-plan-grid .cf-side-card')].map(e=>({top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom}))}));
   assert(m.scrollWidth<=m.width,'Overflow '+name);
   if(m.width>800&&m.cards.length===2)assert(Math.abs(m.cards[0].bottom-m.cards[1].bottom)<2,'Card alignment '+name);
   assert(await target.getByText(/开发演示 · synthetic demo/).count()===0,'Debug panel leaked');
   records.push({name,...m});await target.screenshot({path:path.join(evidence,name+'.png')});
 }
 async function checkHome(target,name){
   await target.getByRole('button',{name:'个人设置',exact:true}).waitFor();await target.waitForTimeout(700);
   const text=await target.locator('body').innerText();
   assert(!text.includes('当前为临时使用；开启个性化后可跨会话保存。'),'Temporary-use notice visible '+name);
   assert(!text.includes('先说一件想做的事，不用先整理成时间表。'),'Removed input help visible '+name);
   assert(!text.includes('个人设置和课表都可以以后再填；保存一次，以后少输入。'),'Removed footer help visible '+name);
   assert(text.includes('只有包含通勤时才需要确认位置。'),'Location help mismatch '+name);
   assert(text.includes(estimateEntry),'Estimate entry missing '+name);
   const viewport=target.viewportSize();
   const campus=await target.getByRole('combobox').first().boundingBox();
   const time=await target.locator('[data-testid="stExpander"]').filter({hasText:'时间设置'}).first().boundingBox();
   if(viewport.width>800)assert(campus&&time&&Math.abs(campus.y-time.y)<=3,'Campus/time controls misaligned '+name);
   await check(name,target);
 }
 async function checkSettings(target,name){
   await target.getByRole('button',{name:'个人设置',exact:true}).click();
   const dialog=target.getByRole('dialog');await dialog.waitFor();
   const close=target.getByRole('button',{name:'关闭个人设置',exact:true});await close.waitFor();
   const dialogBox=await dialog.boundingBox(),closeBox=await close.boundingBox(),viewport=target.viewportSize();
   assert(dialogBox&&dialogBox.y<=1&&dialogBox.y+dialogBox.height>=viewport.height-1,'Settings not full height '+name);
   assert(closeBox&&dialogBox.x+dialogBox.width-(closeBox.x+closeBox.width)<=32,'Close button not at right edge '+name);
   await check(name,target);await close.click();await dialog.waitFor({state:'detached'});await target.waitForTimeout(450);
   assert((await target.evaluate(()=>document.activeElement&&document.activeElement.textContent||'')).includes('个人设置'),'Settings focus was not restored '+name);
 }
 try{
   await page.goto(base+'/?profile=release&identity=anonymous&presentation=1');await checkHome(page,'home-1440');
   const firstCta=await button('帮我安排').boundingBox();assert(firstCta&&firstCta.y+firstCta.height<900,'First action fell below desktop fold');
   assert((await page.locator('body').innerText()).includes('Deploy')===false,'Developer Deploy chrome visible');
   await page.screenshot({path:path.join(screenshots,'home.png')});await check('home-1440');
   await checkSettings(page,'settings-home-1440');
   for(const [width,height] of [[1024,768],[390,844]]){
     const homePage=await browser.newPage({viewport:{width,height}});
     await homePage.goto(base+'/?profile=release&identity=anonymous&presentation=1');
     await checkHome(homePage,'home-'+width);await checkSettings(homePage,'settings-home-'+width);await homePage.close();
   }
   await page.goto(base+'/?profile=release&presentation=1');await button('个人设置').waitFor();await wait();
   await page.getByRole('combobox').first().click();await page.getByRole('option',{name:'卫津路校区',exact:true}).click();await wait();
   await fill('接下来想做什么？',story);await button('帮我安排').click();await page.locator('.cf-plan-grid').waitFor();await wait();
   const timeline=page.locator('.cf-timeline-card');
   const original=await timeline.innerText();
   assert(original.includes('18:50')&&original.includes('18:55')&&original.includes('19:00'),'Class prep facts absent');
   await align(page.locator('.cf-plan-hero'));await check('plan-1440');
   await page.locator('.cf-plan-grid').screenshot({path:path.join(screenshots,'plan.png')});
   await fill('告诉 CampusFlow 发生了什么……','作业还剩40分钟，我现在在图书馆。');
   await button('更新方案').click();await wait();await page.locator('.cf-plan-grid').waitFor();
   const changed=await timeline.innerText();
   assert(changed!==original&&changed.includes('40 分钟')&&changed.includes('14:40'),'Feedback did not update workload');
   assert(changed.includes('19:00')&&changed.includes('20:30'),'Feedback changed fixed class');
   await align(page.locator('.cf-plan-hero'));await check('feedback-1440');
   const focusBox=await page.locator('.cf-plan-hero').boundingBox();
   const feedbackBox=await page.getByText('计划有变化？告诉 CampusFlow 发生了什么',{exact:true}).boundingBox();
   const timelineBox=await page.locator('.cf-timeline-card').boundingBox();
   assert(focusBox&&feedbackBox&&timelineBox&&focusBox.y+focusBox.height<=feedbackBox.y&&feedbackBox.y+feedbackBox.height<=timelineBox.y,
      'Current action, feedback and timeline hierarchy is wrong');
   await page.locator('.cf-plan-grid').screenshot({path:path.join(screenshots,'feedback.png')});
   await page.getByText(estimateEntry,{exact:true}).click();await fill('任务、通知或说明',notice);
   await button('帮我看看').click();await page.getByText(/CampusFlow 整理出了/).waitFor();await wait();
   assert(await input('名称').count()===0,'Summary default became an editor');
   await align(page.getByText(/CampusFlow 整理出了/));await check('material-1440');
   await page.screenshot({path:path.join(screenshots,'material.png')});
   await button('个人设置').click();await page.getByRole('dialog').waitFor();
   const dialogBox=await page.getByRole('dialog').boundingBox();
   assert(dialogBox&&dialogBox.y<=1&&dialogBox.y+dialogBox.height>=899,'Settings drawer does not fill the viewport');
   await fill('还有什么希望 CampusFlow 平时记住？','连续学习久了，希望留一点休息。');
   await button('保存设置').click();await page.getByRole('dialog').waitFor({state:'detached'});await wait();
   await button('个人设置').click();await page.getByRole('dialog').waitFor();
   await align(page.getByRole('tab',{name:'我的偏好',exact:true}));await check('settings-1440');
   await page.screenshot({path:path.join(screenshots,'settings.png')});
   if(syntheticImage){
     await page.getByRole('tab',{name:'我的课表',exact:true}).click();await wait();
     const manual=page.getByRole('checkbox',{name:'启用手动课表',exact:true});
     if((await manual.getAttribute('aria-checked'))!=='true'){
       await page.getByText('启用手动课表',{exact:true}).click();await wait();
     }
     for(const [index,value] of [[0,'2026/08/31'],[1,'2027/01/17']]){
       const date=page.locator('[data-testid="stDateInput"] input').nth(index);
       await date.fill(value);await date.press('Enter');await wait();await date.press('Escape');
     }
     const dialog=page.getByRole('dialog');
     await dialog.locator('input[type=file]').setInputFiles(syntheticImage);
     await page.getByText('synthetic-capability-check.png',{exact:true}).last().waitFor();await wait();
     await page.locator('button:not([disabled])').filter({hasText:'识别课表'}).click();
     await page.getByText('识别预览',{exact:true}).waitFor();await wait();
     await align(page.getByText('识别预览',{exact:true}));await check('timetable-image-1440');
   }
   await page.getByRole('button',{name:/^(关闭个人设置|×)$/}).click();await wait();
   if(syntheticImage){
     const imagePage=await browser.newPage({viewport:{width:1440,height:900}});
     await imagePage.goto(base+'/?profile=release&identity=anonymous&presentation=1');
     await imagePage.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
     await imagePage.locator('input[type=file]').first().setInputFiles(syntheticImage);
     await imagePage.getByText('synthetic-capability-check.png',{exact:true}).waitFor();await wait();
     await imagePage.locator('button:not([disabled])').filter({hasText:/^(帮我看看|重新看看)$/}).click();
     await imagePage.locator('.cf-estimate-result').waitFor();await wait();
     await align(imagePage.locator('.cf-estimate-result'));await check('task-image-1440',imagePage);
     await imagePage.setViewportSize({width:390,height:844});await check('task-image-390',imagePage);
     await imagePage.close();
   }
   for(const [width,height] of [[1024,768],[390,844]]){
      await page.setViewportSize({width,height});await align(page.locator('.cf-plan-hero'));await check('plan-'+width);
      await button('个人设置').click();await page.getByRole('dialog').waitFor();await align(page.getByRole('tab',{name:'我的偏好',exact:true}));await check('settings-'+width);
      await page.getByRole('button',{name:/^(关闭个人设置|×)$/}).click();await wait();
   }
   await page.setViewportSize({width:1440,height:900});await page.reload();await button('个人设置').waitFor();await wait();
   assert((await timeline.innerText()).includes('40 分钟'),'Saved plan did not restore');
   await button('个人设置').click();await page.getByRole('dialog').waitFor();
   assert(await input('还有什么希望 CampusFlow 平时记住？').inputValue()==='连续学习久了，希望留一点休息。','Saved preference did not restore');
   await page.getByRole('button',{name:/^(关闭个人设置|×)$/}).click();await check('restored-1440');
   fs.writeFileSync(path.join(evidence,'observations.json'),JSON.stringify(records,null,2));
   console.log(JSON.stringify({status:'passed',observations:records.map(x=>x.name)}));
 }catch(e){await page.screenshot({path:path.join(evidence,'failure.png')});console.error((await page.locator('body').innerText()).slice(-3500));throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
