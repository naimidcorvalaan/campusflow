/* Bounded offline acceptance: actual renderer, synthetic documents/responses. */
const {chromium}=require('playwright'),fs=require('fs'),path=require('path');
const base=process.env.CAMPUSFLOW_PREVIEW_URL||'http://127.0.0.1:8566/';
if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(base))throw Error('Local preview only');
const out=path.resolve('artifacts/document_material');
const assert=(value,message)=>{if(!value)throw Error(message);};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const observations=[];let page;
 try{
  for(const [story,width] of [['quality',1440],['quality',390],['full',1440],['action',1440],['fallback',1440],['exam',1440],['needs_input',1440]]){
   const context=await browser.newContext({viewport:{width,height:width===390?844:1000}});
   page=await context.newPage();
   const button=name=>page.getByRole('button',{name,exact:true});
   const idle=async()=>{await page.locator('[data-testid="stStatusWidget"]').waitFor({state:'hidden'});await page.waitForTimeout(350);};
   const count=async()=>Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：\s*(\d+)/)[1]);
   const shot=async suffix=>{
    await material.evaluate(el=>{document.querySelector('section.main').scrollTop+=el.getBoundingClientRect().top-70;});
    await page.screenshot({path:path.join(out,`coverage-${story}-${width}-${suffix}.png`)});
   };
   await page.goto(base+`?profile=release&identity=anonymous&documents=1&estimate_rehearsal=${story}`);
   await button('个人设置').waitFor();await idle();
   await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
   const material=page.locator('[data-testid="stExpander"]').filter({has:page.locator('.cf-add-task-marker')});
   const filename=story==='quality'?'本科生综合素质测评考核表【主观评价部分】.docx':story==='exam'?'exam.pdf':story==='needs_input'?'reference.docx':'application.docx';
   await page.locator('input[type=file]').setInputFiles(path.join(out,filename));
   await material.getByText(/已读取材料/).waitFor();await idle();
   assert(await count()===0,'Upload called model');
   await button('帮我看看').click();
   const spinner=page.getByText('THINKING.......',{exact:true});
   await spinner.waitFor();
   assert((await page.locator('[data-testid="stStatusWidget"]').innerText()).includes('THINKING'),'Global status');
   await spinner.waitFor({state:'hidden',timeout:30000});await idle();
   if(story==='needs_input'){
    assert(await material.locator('.cf-material-estimate').count()===0,'Reference invented estimate');
    assert((await material.innerText()).includes('你准备怎么处理'),'Missing intent question');
   }else{
    await material.locator('.cf-material-estimate').waitFor();
    assert(await material.locator('textarea:visible').count()===0,'Default result has textarea');
    assert(await material.getByRole('button',{name:'补充我的情况',exact:true}).count()===1,'Duplicate supplement');
    assert(!await material.locator('[data-testid="stFileUploadDropzone"]').isVisible(),'Uploader competes with result');
    const body=await material.innerText();
    assert((body.split(filename).length-1)===1,'Duplicate filename');
    assert(!/任务身份|没看出具体|完整整理|时间、地点/.test(body),'Engineering/redundant copy');
    if(story==='quality'){
     assert(body.includes('3–5')&&body.includes('4'),'Lost partial estimate');
     assert(body.includes('目前识别到的部分')&&body.includes('可能需要更久'),'Partial presented as whole');
     assert(await button('加入计划').count()===0,'Partial ambiguous action bypasses gate');
    }else assert(body.includes('预计专注用时')&&!body.includes('可能需要更久'),'Whole estimate unnecessarily partial');
   }
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow');
   assert(!(await page.locator('body').innerText()).includes('页面暂时未能打开'),'Page rendering error');
   await shot('result');
   observations.push({story,width,calls:await count(),clean:true});
   if(story==='quality'&&width===1440){
    await button('补充我的情况').click();await material.getByRole('textbox',{name:'补充说明',exact:true}).waitFor();
    await material.getByRole('textbox',{name:'补充说明',exact:true}).fill('分数已经算好，还需要填写自我评价。');
    await material.getByRole('textbox',{name:'补充说明',exact:true}).press('Tab');await idle();
    assert(await material.locator('.cf-material-estimate').count()===1,'Typing erased estimate');
    assert(await count()===2,'Typing called model');
    await button('重新估算').click();await spinner.waitFor();await spinner.waitFor({state:'hidden',timeout:30000});await idle();
    assert(await count()===4,'Reestimate exceeded bounded extraction calls');
    assert(await material.getByRole('textbox',{name:'补充说明',exact:true}).inputValue()==='分数已经算好，还需要填写自我评价。','Success lost supplement');
    assert((await material.innerText()).includes('可能需要更久'),'Retry promoted partial coverage');
    await page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true}).click();
    await page.locator('[data-testid="stSelectbox"]').filter({hasText:'演示服务状态'}).getByRole('combobox').click();
    await page.getByRole('option',{name:'模拟网络失败',exact:true}).click();await idle();
    await button('重新估算').click();
    await spinner.waitFor();await spinner.waitFor({state:'hidden',timeout:30000});await idle();
    assert((await material.innerText()).includes('3–5'),'Failure erased previous estimate');
    await shot('failed-reestimate');
    observations.push({story,typedWithoutCalls:true,retryCalls:2,failurePreserved:true});
   }
   if(story==='full'||story==='fallback'){
    if(story==='fallback'){
     await button('加入计划').click();
     await page.getByText('这是新任务，不是固定安排，没有截止时间或指定执行地点',{exact:true}).click();await idle();
     await button('按20分钟准备加入').click();await button('核对并加入计划').waitFor();await idle();
    }
    await button('核对并加入计划').click();await button('确认并加入计划').waitFor();await idle();
    assert(await count()===(story==='full'?1:2),'Review called model');
    await button('确认并加入计划').click();
    await page.getByText('这份材料已确认并安排。修改上方材料可整理下一份。',{exact:true}).waitFor({state:'attached',timeout:45000});
    observations.push({story,confirmed:true,calls:await count()});
   }
   await context.close();
  }
  fs.writeFileSync(path.join(out,'coverage-observations.json'),JSON.stringify(observations,null,2));
  console.log(JSON.stringify(observations));
 }catch(e){if(page&&!page.isClosed()){await page.screenshot({path:path.join(out,'coverage-failure.png')});console.error((await page.locator('body').innerText()).slice(-5000));}throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
