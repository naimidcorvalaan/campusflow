/* Fresh README assets from the formal UI, isolated fixtures and native controls. */
const fs=require('fs'),path=require('path');
module.exports=async function(browser,base='http://127.0.0.1:8581/') {
  if(!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(base))throw Error('Isolated localhost preview only');
  const out=path.resolve('docs/assets/readme/current'),evidence=path.resolve('artifacts/readme-ui-refresh');
  fs.mkdirSync(out,{recursive:true});fs.mkdirSync(evidence,{recursive:true});
  const ctx=await browser.newContext({viewport:{width:1440,height:940},deviceScaleFactor:2});
  let p=await ctx.newPage();const checks=[];
  const check=(ok,label,value=true)=>{if(!ok)throw Error(label);checks.push({label,value});fs.writeFileSync(path.join(evidence,'browser.json'),JSON.stringify(checks,null,2));};
  const button=name=>p.getByRole('button',{name,exact:true});
  const idle=async()=>{await p.waitForTimeout(450);await p.locator('[data-testid=stStatusWidget]').waitFor({state:'hidden',timeout:30000});await p.waitForTimeout(250);};
  const click=async name=>{await button(name).click();await idle();};
  const shot=async(name,height=940,folder=out)=>{
    await p.setViewportSize({width:1440,height});await p.waitForTimeout(500);
    if(await p.locator('[data-testid=stSidebar]').getAttribute('aria-expanded')!=='true'){await p.locator('[data-testid=collapsedControl]').click();await p.waitForTimeout(400);}
    await p.locator('.cf-environment-summary').click();await idle();await p.evaluate(()=>document.querySelector('section.main').scrollTop=0);await p.waitForTimeout(300);
    const text=await p.locator('body').innerText();
    check(!/离线开发演示|synthetic demo|展示样例|Traceback|API Key/.test(text),'Clean capture '+name);
    await p.screenshot({path:path.join(folder,name+'.png')});
  };
  try {
    await p.goto(base+'?identity=anonymous&profile=visual&presentation=readme');await button('帮我安排').waitFor();await idle();
    await click('切换校区');await click('时间设置');
    const geometry=await p.evaluate(()=>{
      const widget=name=>[...document.querySelectorAll('[data-testid=stSelectbox]')].find(e=>e.querySelector('label')?.innerText===name);
      const metric=(e)=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {x:r.x,y:r.y,bottom:r.bottom,height:r.height,font:s.fontSize,weight:s.fontWeight,color:s.color,border:s.border,radius:s.borderRadius,background:s.backgroundColor};};
      return {labels:[...document.querySelectorAll('.cf-campus-kicker')].map(metric),fields:['当前校区','小时','分钟'].map(n=>metric(widget(n).querySelector('[data-baseweb=select]>div')))};
    });
    for(const key of ['font','weight','color','y','bottom'])check(geometry.labels[0][key]===geometry.labels[1][key],'Shared label '+key);
    for(const key of ['y','height','border','radius','background'])check(geometry.fields.every(x=>x[key]===geometry.fields[0][key]),'Shared control '+key);
    check(Math.abs(geometry.labels[1].x-geometry.fields[1].x)<1,'Time label aligns first control');
    check(geometry.fields[0].y-geometry.labels[0].bottom>=10,'Equal comfortable label gaps',geometry);
    const hour=p.locator('[data-testid=stSelectbox]').filter({hasText:'小时'}).getByRole('combobox');await hour.fill('15');await p.getByRole('option',{name:'15',exact:true}).click();await idle();
    check((await p.locator('.cf-environment-summary').innerText()).includes('15:00'),'Manual hour works');
    const minute=p.locator('[data-testid=stSelectbox]').filter({hasText:'分钟'}).getByRole('combobox');await minute.fill('25');await p.getByRole('option',{name:'25',exact:true}).click();await idle();
    check((await p.locator('.cf-environment-summary').innerText()).includes('15:25'),'Manual minute works');
    await click('使用当前时间');check((await p.locator('.cf-environment-summary').innerText()).includes('14:00'),'Current time reset works');
    await p.locator('[data-testid=stSelectbox]').filter({hasText:'当前校区'}).getByRole('combobox').click();await p.getByRole('option',{name:'卫津路校区',exact:true}).click();await idle();
    await p.getByRole('textbox',{name:'接下来想做什么？',exact:true}).fill('在图书馆写90分钟作业，然后背60分钟单词，可以拆开。19:00去9教上课，上一小时半，上课前吃晚饭。');
    await p.getByRole('textbox',{name:'接下来想做什么？',exact:true}).press('Tab');await idle();
    await shot('plan-input',880);
    await p.getByText('补充当前位置（可选）',{exact:true}).click();await p.getByRole('textbox',{name:'我现在的位置（可选）',exact:true}).fill('图书馆');await p.getByRole('textbox',{name:'我现在的位置（可选）',exact:true}).press('Tab');await idle();
    await click('帮我安排');await p.locator('.cf-focus-action').waitFor();await idle();
    check(await p.locator('.cf-plan-provisional').count()===0,'Published sample has no unresolved question');
    await click('切换校区');await click('时间设置');await shot('current-action',900);await click('今日计划表');await p.locator('.cf-timeline-card').waitFor();await shot('today-plan',1080);
    await p.close();p=await ctx.newPage();
    await p.goto(base+'?identity=anonymous&profile=visual&presentation=readme&documents=1&estimate_rehearsal=workload');await button('任务估时').waitFor();await click('任务估时');await button('选择文件').waitFor();
    check(await p.getByRole('textbox',{name:'或用文字描述你的任务',exact:true}).isVisible(),'New task text label');
    const dz=p.locator('[data-testid=stFileUploadDropzone]:visible');
    const layout=await dz.evaluate(e=>{const s=getComputedStyle(e),b=e.querySelector('button'),r=b.getBoundingClientRect(),d=e.getBoundingClientRect();return {direction:s.flexDirection,wrap:s.flexWrap,gap:s.gap,height:d.height,buttonTop:r.y-d.y,before:getComputedStyle(e,'::before').content,after:getComputedStyle(e,'::after').content};});
    check(layout.direction==='row'&&layout.height<130&&layout.buttonTop<25,'Inline native upload row with compact guidance',layout);
    await shot('upload-desktop',940,evidence);
    await p.setViewportSize({width:390,height:844});await p.waitForTimeout(600);
    if(await p.locator('[data-testid=stSidebar]').getAttribute('aria-expanded')==='true'){await p.locator('[data-testid=stSidebarContent]>div:first-child button').click();await p.waitForTimeout(400);}
    check(await p.evaluate(()=>document.querySelector('section.main').scrollWidth<=document.querySelector('section.main').clientWidth+1),'Upload no mobile overflow');
    await p.screenshot({path:path.join(evidence,'upload-mobile.png')});await p.setViewportSize({width:1440,height:940});
    const choosing=p.waitForEvent('filechooser');await button('选择文件').click();await(await choosing).setFiles(path.resolve('artifacts/document_material/workload/assessment.docx'));await p.getByText(/已读取材料/).waitFor();await idle();
    await click('补充我的情况');await p.getByRole('textbox',{name:'补充说明',exact:true}).fill('填表');await click('帮我看看');await p.locator('.cf-material-estimate').waitFor();await idle();
    check((await p.locator('.cf-material-estimate').innerText()).includes('35–65'),'Existing estimation result unchanged');
    await click('补充我的情况');await shot('task-estimate',1040);
    await ctx.close();return checks;
  } catch(e) {await p.screenshot({path:path.join(evidence,'failure.png')});fs.writeFileSync(path.join(evidence,'failure.txt'),String(e)+'\n'+await p.locator('body').innerText());await ctx.close();throw e;}
};
