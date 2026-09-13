/* Real live rendering and local file adapters, finite mock responses only. */
const {chromium}=require('playwright');
const path=require('path'),fs=require('fs');
const out=path.resolve('artifacts/document_material');
const base=process.env.CAMPUSFLOW_PREVIEW_URL || 'http://127.0.0.1:8561/';
if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+\//.test(base)) throw Error('Local preview only');
const assert=(ok,message)=>{if(!ok)throw Error(message);};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const observations=[];
 try{
  for(const [file,width] of [['task.docx',1440],['text.pdf',1440],['scan.pdf',1440],['mixed.pdf',390],['long-scan.pdf',1440],['old.doc',1440]]){
   const context=await browser.newContext({viewport:{width,height:width===390?844:900}});
   const page=await context.newPage();
   const wait=()=>page.waitForTimeout(800);
   const button=name=>page.getByRole('button',{name,exact:true});
   const count=async()=>Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：\s*(\d+)/)[1]);
   const shot=async suffix=>{await page.screenshot({path:path.join(out,file.replace('.','-')+'-'+suffix+'.png')});};
   await page.goto(base+'?profile=release&identity=anonymous&documents=1');
   await button('个人设置').waitFor();await wait();
   await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
   await page.locator('input[type=file]').setInputFiles(path.join(out,file));await wait();
   assert(await count()===0,'Upload called model');
   if(file==='old.doc'){
    await page.getByText('暂不支持 .doc，请另存为 .docx 后再试。',{exact:true}).waitFor();
    observations.push({file,unsupported:true,calls:await count()});await context.close();continue;
   }
   if(file==='long-scan.pdf'){
    await page.getByText(/这份PDF需要查看的图文页较多/).waitFor();
    await page.getByRole('textbox',{name:'要分析的页码（可选，留空读取整份）',exact:true}).fill('1-2');
    await page.getByRole('textbox',{name:'要分析的页码（可选，留空读取整份）',exact:true}).press('Tab');await wait();
   }
   await button('帮我看看').scrollIntoViewIfNeeded();await shot('upload');
   await button('帮我看看').click();
   await page.getByText(/CampusFlow 整理出了 [12] 项 · 尚未加入/).waitFor({timeout:20000});await wait();
   assert(await count()===1,'Extraction must use one model call');
   await page.getByText(/CampusFlow 整理出了/).scrollIntoViewIfNeeded();await shot('result');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow');
   if(file==='task.docx'){
    await button('修改').first().click();await wait();
    const minutes=page.getByRole('textbox',{name:'采用分钟（可留空）',exact:true});
    await minutes.fill('80');await minutes.press('Tab');await wait();
    assert(await count()===1,'Edit called model');
    await shot('edited');
   }
   if(file==='text.pdf'){
    const selected=page.getByRole('checkbox',{name:/选择第[12]项/});
    assert(await selected.count()===2,'Expected two separate PDF tasks');
    await page.getByText('选择第2项：实验二报告',{exact:true}).click();await wait();
    assert(!(await selected.nth(1).isChecked()),'Second item was not deselected');
    assert(await count()===1,'Selection called model');
   }
   await button('确认并加入计划').click();await wait();
   await page.getByText('这份材料已确认并安排。修改上方材料可整理下一份。',{exact:true}).waitFor({timeout:15000,state:'attached'});
   await shot('confirmed');
   observations.push({file,width,extractionCalls:1,confirmed:true,totalCalls:await count()});
   await context.close();
  }
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const page=await context.newPage();
  await page.goto(base+'?profile=final&documents=1');
  await page.getByRole('button',{name:'个人设置',exact:true}).waitFor();
  await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
  await page.locator('input[type=file]').setInputFiles(path.join(out,'task.docx'));
  await page.waitForTimeout(800);
  await page.getByRole('button',{name:'帮我看看',exact:true}).click();
  await page.getByText(/CampusFlow 整理出了 1 项 · 尚未加入/).waitFor();
  await page.reload();await page.waitForTimeout(1800);
  await page.getByText('原文件不保存在档案中；已整理的事项仍在，需要重新读取时请再次选择文件。',{exact:true}).waitFor();
  observations.push({restoredFileDraft:true,originalUploadAbsent:await page.locator('input[type=file]').evaluate(e=>e.files.length===0)});
  await context.close();
  fs.writeFileSync(path.join(out,'browser-evidence.json'),JSON.stringify(observations,null,2));
  console.log(JSON.stringify(observations));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1);});
