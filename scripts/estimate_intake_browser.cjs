const {chromium}=require('playwright'),fs=require('fs'),path=require('path');
const base='http://127.0.0.1:8566/';
const before=process.argv.includes('--before');
const out=path.resolve('artifacts/document_material');
const assert=(v,m)=>{if(!v)throw Error(m);};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),observations=[];
 try{
  const button=name=>page.getByRole('button',{name,exact:true});
  const idle=async()=>{await page.locator('[data-testid="stStatusWidget"]').waitFor({state:'hidden'});await page.waitForTimeout(350);};
  await page.goto(base+'?profile=release&identity=anonymous&documents=1&estimate_rehearsal=intake_chain');
  await button('个人设置').waitFor();await idle();
  await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
  const area=page.locator('[data-testid="stExpander"]').filter({has:page.locator('.cf-add-task-marker')});
  await page.locator('input[type=file]').setInputFiles(path.join(out,'本科生综合素质测评考核表【主观评价部分】.docx'));
  await area.getByText(/已读取材料/).waitFor();await idle();
  async function observe(stage){
   const state=await area.evaluate(root=>{
    const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
    return {inputs:[...root.querySelectorAll('textarea')].filter(visible).map(e=>({label:e.getAttribute('aria-label'),value:e.value,disabled:e.disabled})),
      buttons:[...root.querySelectorAll('button')].filter(visible).map(e=>e.textContent.trim()),
      estimates:root.querySelectorAll('.cf-material-estimate').length,text:root.innerText};
   });
   observations.push({stage,...state});
   await area.evaluate(el=>{document.querySelector('section.main').scrollTop+=el.getBoundingClientRect().top-70;});
   await page.screenshot({path:path.join(out,`intake-${before?'before':'after'}-${stage}.png`)});
   return state;
  }
  async function submit(label,tag){
   await observe(tag+'-input');
   await button(label).click();
   const spinner=page.getByText('THINKING.......',{exact:true});
   await spinner.waitFor();
   const sample=await observe(tag+'-processing');
   const peaks=[];
   for(let i=0;i<10;i++){
    peaks.push(await area.locator('textarea[aria-label="补充说明"]:visible').count());
    await page.waitForTimeout(100);
   }
   observations.push({stage:tag+'-processing-samples',peaks});
   if(!before){assert(Math.max(...peaks)<=1,'Duplicate input during processing');assert(sample.buttons.filter(x=>x===label).length<=1,'Duplicate submit');}
   await spinner.waitFor({state:'hidden',timeout:30000});await idle();
   const result=await observe(tag+'-result');
   if(!before)assert(result.estimates===1,'Readable form failed to estimate');
   return result;
  }
  await submit('帮我看看','upload');
  await button('补充我的情况').click();
  await page.getByRole('textbox',{name:'补充说明',exact:true}).fill('填表');
  // Deliberately submit by clicking while the textarea is focused, like a user.
  const final=await submit(before?'帮我看看':'重新估算','supplement');
  if(!before){assert(final.inputs.length===1&&final.inputs[0].value==='填表','Supplement disappeared or collapsed');assert(!final.text.includes('你准备怎么处理'),'Repeated intent question');}
  if(!before){
   await page.getByRole('textbox',{name:'补充说明',exact:true}).fill('填表，分数已经算好');
   await button('补充我的情况').click();await idle();
   await button('补充我的情况').click();await idle();
   assert(await page.getByRole('textbox',{name:'补充说明',exact:true}).inputValue()==='填表，分数已经算好','Closing editor lost unsent text');
  }
  await page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true}).click();
  const requests=JSON.parse(await page.locator('pre').last().innerText());
  observations.push({requests});
  if(!before){assert(requests.length===4,'Unbounded or missing semantic correction');assert(requests.slice(2).every(x=>x.supplement==='填表'&&x.source_present),'Supplement/source lost on model call');}
  fs.writeFileSync(path.join(out,`intake-${before?'before':'after'}-observations.json`),JSON.stringify(observations,null,2));
  console.log(JSON.stringify(observations.map(x=>({stage:x.stage,inputs:x.inputs,estimates:x.estimates,peaks:x.peaks,requests:x.requests}))));
 }catch(e){await page.screenshot({path:path.join(out,'intake-failure.png')});console.error((await page.locator('body').innerText()).slice(-4000));throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
