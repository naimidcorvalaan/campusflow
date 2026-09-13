/* Finite local-only rehearsal through the formal live page and lowest fake caller. */
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path');
const base=process.env.CAMPUSFLOW_PREVIEW_URL||'http://127.0.0.1:8563/';
if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(base))throw Error('Local preview only');
const out=path.resolve('artifacts/document_material');
const assert=(value,message)=>{if(!value)throw Error(message);};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const observations=[];
 let latest;
 try{
 for(const [story,width] of [['full',1440],['fallback',1440],['needs_input',1440],['exam',1440],['fallback',390],['action',1440],['action',390],['action_broken',390]]){
  const ctx=await browser.newContext({viewport:{width,height:width===390?844:900}}),page=await ctx.newPage();
  latest=page;
  const button=name=>page.getByRole('button',{name,exact:true});
  const wait=()=>page.waitForTimeout(800);
  const shot=name=>page.screenshot({path:path.join(out,`recovery-${story}-${width}-${name}.png`)});
  const count=async()=>Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：\s*(\d+)/)[1]);
  await page.goto(base+`?profile=release&identity=anonymous&documents=1&estimate_rehearsal=${story}`);
  await button('个人设置').waitFor();await wait();
  await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
  const filename=story==='exam'?'exam.pdf':story==='needs_input'?'reference.docx':'application.docx';
  await page.locator('input[type=file]').setInputFiles(path.join(out,filename));await wait();
  const material=page.locator('[data-testid="stExpander"]').filter({has:page.locator('.cf-add-task-marker')});
  await material.getByText(/已读取材料/).waitFor();
  await page.locator('[data-testid="stStatusWidget"]').waitFor({state:'hidden'});
  assert(await material.locator('textarea').count()===0,'Uploaded material still has large empty text boxes');
  assert((await material.innerText()).split(filename).length-1===1,'Duplicate filename');
  const drop=await material.locator('[data-testid="stFileUploadDropzone"]').boundingBox();
  observations.push({story,width,uploadHeight:drop.height,filenameOccurrences:1,optionalFieldsCollapsed:true});
  assert(drop.height<150,'Uploader is too tall');
  await shot('uploaded');
  assert(await count()===0,'Upload called model');
  await button('帮我看看').click();
  const spinner=page.getByText('THINKING.......',{exact:true});
  await spinner.waitFor();await spinner.scrollIntoViewIfNeeded();await shot('thinking');
  const global=await page.locator('[data-testid="stStatusWidget"]').innerText();
  observations.push({story,width,local:await spinner.innerText(),global});
  assert(global.includes('THINKING')&&!/running/i.test(global),'Wrong global status: '+global);
  const result=story==='needs_input'?page.getByText(/你准备怎么处理这份材料/):page.locator('.cf-material-estimate').first();
  await result.waitFor({timeout:30000});await wait();
  await result.evaluate(el=>{const area=document.querySelector('section.main');area.scrollTop+=el.getBoundingClientRect().top-100;});
  await shot('result');
  assert(!(await page.locator('body').innerText()).includes('页面暂时未能打开'),'Page boundary caught a rendering error');
  assert(await spinner.count()===0,'Spinner not cleared');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow');
  assert(await count()===(['fallback','action_broken','needs_input'].includes(story)?2:1),'Unexpected extraction calls');
  if(story.startsWith('action')){
   assert((await material.innerText()).includes('外部等待不计入专注用时'),'Waiting time missing');
   assert(await button('加入计划').count()===(story==='action'?1:0),'Unsafe promotion gate');
  }
  if(story==='fallback'){
   await button('加入计划').click();await wait();
   await page.getByText('这是新任务，不是固定安排，没有截止时间或指定执行地点',{exact:true}).click();await wait();
   assert(await count()===2,'Consent called model');
   await button('按20分钟准备加入').click();
   await button('核对并加入计划').waitFor();await button('核对并加入计划').click();await page.getByText(/CampusFlow 整理出了 1 项/).waitFor();await wait();
   assert(await count()===2,'Preparing draft called model');
   await button('确认并加入计划').click();
   await page.getByText('这份材料已确认并安排。修改上方材料可整理下一份。',{exact:true}).waitFor({state:'attached',timeout:40000});await wait();
   observations.push({story,width,confirmed:true,calls:await count()});
  }
  if(story==='full'){
   // Main planner waiting state using the existing finite standard story.
   await page.getByRole('textbox',{name:'接下来想做什么？',exact:true}).fill('在图书馆写90分钟作业，然后背60分钟单词，可以拆开。19:00去9教上课，上一小时半，上课前吃晚饭。');
   await button('帮我安排').click();await spinner.waitFor();await spinner.scrollIntoViewIfNeeded();await shot('main-thinking');
   await page.locator('.cf-timeline-card').waitFor({timeout:60000});await wait();
   await button('个人设置').click();await page.getByRole('dialog',{name:'个人设置'}).waitFor();
   await page.getByRole('tab',{name:'我的课表',exact:true}).click();
   await page.getByText('启用手动课表',{exact:true}).click();await wait();
   for(const [index,value] of [[0,'2026/08/31'],[1,'2027/01/17']]){
    const input=page.locator('[data-testid="stDateInput"] input').nth(index);
    await input.fill(value);await input.press('Enter');await wait();await input.press('Escape');
   }
   await page.getByRole('textbox',{name:'课表文字',exact:true}).fill('大学英语，周二10:00–11:30，第1–16周，卫津路校区，第九教学楼');
   await page.getByRole('textbox',{name:'课表文字',exact:true}).press('Tab');await wait();
   await button('识别课表').click();
   const inside=page.getByRole('dialog',{name:'个人设置'}).getByText('THINKING.......',{exact:true});
   await inside.waitFor();await inside.scrollIntoViewIfNeeded();await shot('timetable-thinking');
   await page.getByText('识别预览',{exact:true}).waitFor({timeout:15000});await wait();
   assert(await inside.count()===0,'Timetable spinner not cleared');
   observations.push({story,settingsSpinnerInside:true});
  }
  await ctx.close();
 }
 fs.writeFileSync(path.join(out,'recovery-observations.json'),JSON.stringify(observations,null,2));
 console.log(JSON.stringify(observations));
 }catch(e){if(latest&&!latest.isClosed()){await latest.screenshot({path:path.join(out,'recovery-browser-failure.png')});console.error((await latest.locator('body').innerText()).slice(-4000));}throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
