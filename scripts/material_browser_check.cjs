/* Dirty text materials through real Streamlit controls and production handlers. */
const {chromium} = require('playwright-core');
const fs = require('fs'), path = require('path');
const notices = {
 dirty_a:'@所有人 第三章作业周五之前交哈，3-12，8题过程写全，还是学习通那个入口，别忘了',
 dirty_b:'老王说数据结构实验一好像下周一之前交，具体要求群文件里有，我还没看',
 dirty_c:'这周高数3-12周五交，数据结构实验下周一交，然后周四下午两点33教开班会，大概一个小时',
 dirty_d:'今天课真的坐牢哈哈哈，对了老师最后说那个小论文国庆前交，主题自己选，3000字左右，具体格式之后发',
 dirty_e:'高数作业补充一下，第8题必须写完整过程，截止还是周五晚上十点',
 dirty_f:'明晚之前交',
 existing_old:'高数第三章作业，第3—12题，第8题写完整过程，2026-09-10 22:00前交。',
 a:'@全体同学 高数第三章第3—12题，第8题写完整过程，2026-09-11 22:00前交。'
};
const output=path.resolve('artifacts/material_preview');
const edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const assert=(ok,msg)=>{if(!ok)throw Error(msg);};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:edge});
 fs.mkdirSync(output,{recursive:true});
 const observations=[];
 async function open(story,width=1440,height=900){
   const page=await browser.newPage({viewport:{width,height}});
   await page.goto(`http://127.0.0.1:8541/?identity=anonymous&material=${story}`);
   await page.getByRole('button',{name:'个人设置',exact:true}).waitFor();
   return page;
 }
 const input=(page,name)=>page.getByRole('textbox',{name,exact:true});
 const button=(page,name)=>page.getByRole('button',{name,exact:true});
 const wait=page=>page.waitForTimeout(650);
 async function calls(page){
   const value=await page.getByText(/本次浏览器会话的 mock 调用：/).textContent();
   return Number(value.match(/调用：(\d+)/)[1]);
 }
 async function paste(page,story){
   await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
   await input(page,'通知或任务说明').fill(notices[story]);
   await input(page,'通知或任务说明').press('Tab');await wait(page);
   await page.getByRole('button',{name:/^(帮我看看|重新看看)$/}).click();
   await page.getByText(/CampusFlow 整理出了/).waitFor();await wait(page);
 }
 async function shot(page,name,extra={}){
   const size=await page.evaluate(()=>({width:innerWidth,height:innerHeight,
     scrollWidth:document.documentElement.scrollWidth}));
   assert(size.scrollWidth<=size.width,'horizontal overflow '+name);
   observations.push({name,...size,calls:await calls(page),...extra});
   await page.screenshot({path:path.join(output,name+'.png')});
 }
 try{
   let page=await open('dirty_a'); await paste(page,'dirty_a');
   assert(await input(page,'名称').count()===0,'default result became a form');
   await page.getByText(/CampusFlow 整理出了/).scrollIntoViewIfNeeded();
   await shot(page,'dirty-a-summary-1440',{items:1,required:'通知日期与具体截止，或暂不设置；用时可延后'});
   await button(page,'修改').click();await input(page,'名称').waitFor();
   await shot(page,'dirty-a-edit-1440',{editing:true});await page.close();

   page=await open('dirty_b');await paste(page,'dirty_b');
   assert(await page.getByText(/具体要求还在群文件里，可以以后补充/).count()===1,'nonblocking note missing');
   await page.getByText(/CampusFlow 整理出了/).scrollIntoViewIfNeeded();
   await shot(page,'dirty-b-uncertain-1440',{items:1,required:'不确定截止的处理'});await page.close();

   page=await open('dirty_c');await paste(page,'dirty_c');
   assert(await page.getByText(/CampusFlow 整理出了 3 项/).count()===1,'multi split failed');
   await page.getByText(/CampusFlow 整理出了/).scrollIntoViewIfNeeded();
   await shot(page,'dirty-c-multi-1440',{items:3,required:'通知日期；两个截止缺具体钟点'});await page.close();

   page=await open('dirty_d');await paste(page,'dirty_d');
   assert(await page.getByText(/具体格式之后才发布/).count()===1,'future format invented or blocked');
   await page.getByText(/CampusFlow 整理出了/).scrollIntoViewIfNeeded();
   await shot(page,'dirty-d-chat-1440',{items:1,required:'国庆前的具体截止，或暂不设置'});await page.close();

   page=await open('existing_old');await paste(page,'existing_old');await button(page,'帮我估时').click();
   await page.getByText(/专注用时/).waitFor();await button(page,'确认并安排').click();await wait(page);
   await input(page,'告诉 CampusFlow 发生了什么……').fill('高数作业我已经做了20分钟。');
   await button(page,'更新方案').click();await wait(page);
   if(await input(page,'通知或任务说明').count()===0)
     await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
   await input(page,'通知或任务说明').fill(notices.dirty_e);
   await input(page,'通知或任务说明').press('Tab');await wait(page);
   await page.getByRole('button',{name:/^(帮我看看|重新看看)$/}).click();
   await page.getByText(/CampusFlow 整理出了/).waitFor();
   await input(page,'这条通知是什么时候收到的？').fill('2026-09-07');
   await input(page,'这条通知是什么时候收到的？').press('Tab');await wait(page);
   await button(page,'按这个日期确定时间').click();await wait(page);
   await page.locator('div[data-baseweb="select"]').getByText('请选择',{exact:true}).click();
   await page.getByRole('option',{name:/补充现有任务/}).click();await wait(page);
   assert(await page.getByText(/实际完成20分钟/).count()===1,'progress not shown');
   assert(await page.getByText(/有信息与已有任务不同/).count()===1,'deadline conflict not exposed');
   await page.getByText(/有信息与已有任务不同/).scrollIntoViewIfNeeded();
   await shot(page,'dirty-e-existing-1440',{items:1,required:'补充或新建、截止新旧选择'});await page.close();

   page=await open('dirty_f');await paste(page,'dirty_f');
   await shot(page,'dirty-f-relative-1440',{items:1,required:'材料内容、通知日期和截止钟点'});await page.close();

   page=await open('a',390,844);await paste(page,'a');
   assert(await input(page,'名称').count()===0,'narrow summary became a form');
   await button(page,'帮我估时').click();await page.getByText(/专注用时/).waitFor();
   await button(page,'确认并安排').click();await page.getByText(/这份材料已确认并安排/).waitFor({state:'attached'});
   await shot(page,'single-confirm-390',{items:1,confirmed:true});await page.close();

   page=await open('dirty_c',390,844);await paste(page,'dirty_c');
   await shot(page,'multi-list-390',{items:3,confirmed:false});await page.close();
   fs.writeFileSync(path.join(output,'dirty-observations.json'),JSON.stringify(observations,null,2));
   console.log(JSON.stringify(observations));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack||error.message);process.exitCode=1;});
