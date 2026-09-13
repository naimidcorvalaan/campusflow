/* Local waiting-state and failure-cleanup check; no production model. */
const {chromium}=require('playwright'),path=require('path'),fs=require('fs');
const base=process.env.CAMPUSFLOW_PREVIEW_URL||'http://127.0.0.1:8565/';
if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(base))throw Error('Local preview only');
const out=path.resolve('artifacts/document_material');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const observations=[];
 try{
 for(const story of ['full','fallback']){
  const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage();
  const button=name=>page.getByRole('button',{name,exact:true});
  const wait=()=>page.waitForTimeout(800);
  const spinner=page.getByText('THINKING.......',{exact:true});
  const observe=async name=>{
   await spinner.waitFor();await spinner.scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(out,'recovery-'+name+'.png')});
   observations.push({name,local:await spinner.innerText(),global:await page.locator('[data-testid="stStatusWidget"]').innerText()});
   await spinner.waitFor({state:'hidden',timeout:60000});await wait();
  };
  await page.goto(base+`?profile=release&identity=anonymous&documents=1&estimate_rehearsal=${story}`);
  await button('个人设置').waitFor();await wait();
  if(story==='full'){
   await page.getByRole('textbox',{name:'接下来想做什么？',exact:true}).fill('在图书馆写90分钟作业，然后背60分钟单词，可以拆开。19:00去9教上课，上一小时半，上课前吃晚饭。');
   await page.getByRole('textbox',{name:'我现在的位置（可选）',exact:true}).fill('图书馆');
   await button('帮我安排').click();await observe('main-final-thinking');
   await page.getByRole('textbox',{name:'告诉 CampusFlow 发生了什么……',exact:true}).fill('如果改成先背单词，再写作业，会怎么安排？先给我看看，暂时不要改。');
   await button('更新方案').click();await observe('feedback-whatif-thinking');
   observations.push({name:'whatif',preview:await button('保持现在方案').count()===1});
  }else{
   await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
   await page.locator('input[type=file]').setInputFiles(path.join(out,'application.docx'));await wait();
   await button('帮我看看').click();await page.locator('.cf-material-estimate').waitFor({timeout:20000});await wait();
   await page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true}).click();
   await page.locator('[data-testid="stSelectbox"]').filter({hasText:'演示服务状态'}).getByRole('combobox').click();
   await page.getByRole('option',{name:'模拟网络失败',exact:true}).click();await wait();
   await button('补充我的情况').click();await button('重新估算').click();await observe('reestimate-failure-thinking');
   if(await spinner.count())throw Error('Failed call left spinner mounted');
   const body=await page.locator('body').innerText();
   if(body.includes('页面暂时未能打开'))throw Error('Unexpected rendering failure');
   observations.push({name:'failed-reestimate',estimateRetained:/15–30\s*分钟/.test(body),spinnerCleared:true});
  }
  await context.close();
 }
 fs.writeFileSync(path.join(out,'recovery-waiting-observations.json'),JSON.stringify(observations,null,2));
 console.log(JSON.stringify(observations));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
