/* Production Streamlit renderer + finite offline model. No prototype server. */
const fs = require('fs'), path = require('path');
module.exports = async function workbenchCheck(browser, base='http://127.0.0.1:8577/') {
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(base)) throw Error('Local offline entry only');
  const out = path.resolve('artifacts/workbench-migration'); fs.mkdirSync(out,{recursive:true});
  const results=[]; let page;
  const record=(name,value=true)=>{results.push({name,value});fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(results,null,2));};
  const assert=(value,message)=>{if(!value)throw Error(message);};
  const idle=async()=>{await page.locator('[data-testid="stStatusWidget"]').waitFor({state:'hidden',timeout:30000});await page.waitForTimeout(220);};
  const displayName=name=>name==='材料估时'?'任务估时':name;
  const button=name=>page.getByRole('button',{name:displayName(name),exact:true});
  const nav=async(name)=>{
    if(await page.locator('[data-testid="stSidebar"]').getAttribute('aria-expanded')==='false')await page.locator('[data-testid="collapsedControl"]').click();
    await button(name).click();await idle();
    assert((await page.locator('.cf-workspace-header').innerText()).includes(displayName(name)),'Wrong active view');
    if(page.viewportSize().width<800 && await page.locator('[data-testid="stSidebar"]').getAttribute('aria-expanded')==='true') {
      await page.locator('[data-testid="stSidebarContent"]>div:first-child button').click();
    }
  };
  const shot=async(name,fullPage=false)=>{
    await page.evaluate(()=>{document.querySelector('section.main').scrollTop=0;const panel=document.querySelector('[data-campusflow-dialog]');if(panel)panel.scrollTop=0;});
    await page.screenshot({path:path.join(out,name+'.png'),fullPage});
  };
  const openTool=async()=>page.locator('[data-testid="stVerticalBlock"]').filter({has:page.locator(':scope > [data-testid="element-container"] .cf-add-task-marker')});
  const probe=async()=>{
    const exp=page.locator('details').filter({has:page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true})});
    if(!await exp.evaluate(e=>e.open))await exp.locator('summary').first().click();
    const raw=(await page.locator('pre').allTextContents()).find(t=>t.includes('"material_attached"'));
    assert(raw,'No read-only offline state probe');const value=JSON.parse(raw);
    await exp.locator('summary').first().click();return value;
  };
  const noOverflow=async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.querySelector('section.main').scrollWidth<=document.querySelector('section.main').clientWidth+1),'Horizontal overflow');
  const newPage=async(query)=>{
    const context=await browser.newContext({viewport:{width:1440,height:900}});context.setDefaultTimeout(6000);
    page=await context.newPage();await page.goto(base+'?identity=anonymous&ui_probe=1&'+query);await button('材料估时').waitFor({timeout:20000});await idle();
  };
  try {
    await newPage('profile=visual&story=plan&ui_delay=1');
    await shot('01-empty-1440');
    assert(!(await page.locator('body').innerText()).includes('路线进计划'),'Capability promo remains');
    await button('切换校区').click();await idle();
    await page.getByRole('combobox',{name:'当前校区'}).click();await page.getByRole('option',{name:'卫津路校区',exact:true}).click();await idle();
    await button('切换校区').click();await idle();
    await page.getByText('补充当前位置（可选）',{exact:true}).click();
    await page.getByRole('textbox',{name:'我现在的位置（可选）',exact:true}).fill('图书馆');
    await button('帮我安排').click();await idle();
    await page.locator('.cf-focus-minutes').waitFor();
    const published=await probe();
    assert((await page.locator('.cf-focus-action').innerText())==='写作业','Current action missing');
    assert((await page.locator('.cf-side-card').innerText()).includes('18:46 出发'),'Fixed departure missing');
    await shot('02-current-1440');record('current action and published departures');
    await nav('时间线');await shot('03-timeline-1440');
    const timeline=await page.locator('.cf-timeline-card').innerText();
    for(const fact of ['写作业','收拾东西','步行去学三食堂','吃晚饭','进楼找教室','课前准备','19:00–20:30'])assert(timeline.includes(fact),'Lost timeline fact '+fact);
    assert((await probe()).plan===published.plan,'Navigation changed published plan');record('continuous timeline and unchanged refs/progress');
    await nav('今天');
    await page.getByText('更新变化',{exact:true}).click();
    await page.getByRole('textbox',{name:'进度、位置或临时变化',exact:true}).fill('如果改成先背单词，再写作业，会怎么安排？先给我看看，暂时不要改。');
    await button('更新方案').click();await idle();
    await page.locator('.cf-whatif-card').waitFor();await shot('04-what-if-1440');
    assert((await probe()).plan===published.plan,'What-if polluted published plan');
    await button('采用这个调整').click();await idle();
    assert((await probe()).plan!==published.plan,'Atomic adoption did not publish');record('feedback What-if preview isolated and adoption published');
    const adopted=await probe();
    const demo=page.locator('details').filter({has:page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true})});
    await demo.locator('summary').first().click();
    await page.locator('[data-testid="stSelectbox"]').filter({hasText:'演示服务状态'}).getByRole('combobox').click();await page.getByRole('option',{name:'模拟网络失败',exact:true}).click();await idle();
    await demo.locator('summary').first().click();
    const updates=page.locator('details').filter({has:page.locator('.cf-feedback-heading')});
    if(!await updates.evaluate(e=>e.open))await updates.locator('summary').first().click();
    await page.getByRole('textbox',{name:'进度、位置或临时变化',exact:true}).fill('作业又做了20分钟');
    await button('更新方案').click();await idle();await shot('20-service-error-1440');
    assert((await probe()).plan===adopted.plan,'Failed feedback changed published plan');record('failed update keeps published plan and progress');
    await demo.locator('summary').first().click();
    await page.locator('[data-testid="stSelectbox"]').filter({hasText:'演示服务状态'}).getByRole('combobox').click();await page.getByRole('option',{name:'正常',exact:true}).click();await idle();await demo.locator('summary').first().click();
    await button('个人设置').click();await idle();
    const dialog=page.getByRole('dialog');
    const bounds=await dialog.boundingBox();assert(bounds.width>=1440*.44&&bounds.width<=1440*.49,'Drawer width');
    await shot('05-settings-1440');await page.getByRole('tab',{name:'常用地点',exact:true}).click();await shot('06-places-1440');
    await page.getByRole('tab',{name:'我的课表',exact:true}).click();
    await page.getByText('启用手动课表',{exact:true}).click();await idle();
    await page.getByRole('textbox',{name:'第1教学周的周一',exact:true}).fill('2026/08/31');await page.getByRole('textbox',{name:'第1教学周的周一',exact:true}).press('Enter');await idle();
    await page.getByRole('textbox',{name:'有效结束日期',exact:true}).fill('2026/12/27');await page.getByRole('textbox',{name:'有效结束日期',exact:true}).press('Enter');await idle();
    await page.getByRole('textbox',{name:'课表文字',exact:true}).fill('大学英语，周二10:00–11:30，第1–16周，卫津路校区，第九教学楼');await page.getByRole('textbox',{name:'课表文字',exact:true}).press('Tab');await idle();
    await shot('07-timetable-1440');await button('识别课表').click();
    await page.getByText('THINKING.......',{exact:true}).waitFor();await shot('08-course-thinking-1440');await idle();
    await page.locator('.cf-import-preview-title').waitFor();
    await page.getByRole('textbox',{name:'课程名',exact:true}).waitFor();
    await page.getByRole('textbox',{name:'课表文字',exact:true}).press('Tab');
    await shot('09-course-preview-1440');
    await button('关闭个人设置').click();await idle();record('grouped settings, timetable native import, thinking and close');
    await page.setViewportSize({width:390,height:844});await shot('10-current-390');await noOverflow();
    await nav('时间线');await shot('11-timeline-390');await noOverflow();
    if(await page.locator('[data-testid="stSidebar"]').getAttribute('aria-expanded')==='false')await page.locator('[data-testid="collapsedControl"]').click();
    await button('个人设置').click();await idle();await shot('12-settings-390');await noOverflow();
    assert(await page.getByRole('textbox',{name:'第1教学周的周一',exact:true}).inputValue()==='2026/08/31','Date draft lost on reopen');
    const close=await button('关闭个人设置').boundingBox();assert(close.x>=0&&close.x+close.width<=390&&close.width>=43,'Close unreachable');
    await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'detached'});await idle();record('390 current/timeline/settings and reachable close');
    await page.context().close();

    for(const story of ['workload','workload_partial','workload_empty','needs_input']) {
      await newPage('profile=release&documents=1&estimate_rehearsal='+story);await nav('材料估时');
      await shot('13-material-input-1440');await openTool();
      const file=path.resolve('artifacts/document_material/workload/'+(story==='needs_input'?'reference.docx':story==='workload_partial'?'assessment-partial.docx':'assessment.docx'));
      await page.locator('input[type=file]').setInputFiles(file);
      // File transfer and its reader rerun can begin after the global status
      // briefly disappears. Wait for the actual reader result, not that gap.
      await page.getByText(/已读取材料/).waitFor({state:'attached',timeout:15000});
      await idle();await openTool();
      if(story==='workload_empty') {
        await button('补充我的情况').click();await idle();await page.getByRole('textbox',{name:'补充说明',exact:true}).fill('填表');await page.getByRole('textbox',{name:'补充说明',exact:true}).press('Tab');await idle();
        const before=await probe();await nav('今天');await nav('材料估时');await button('个人设置').click();await idle();await button('关闭个人设置').click();await idle();
        const after=await probe();assert(after.material_attached&&after.material_bytes===before.material_bytes,'Drawer lost server-side upload');assert(after.supplement==='填表'&&after.calls===before.calls,'Navigation lost intent or called model');record('upload survives navigation and settings close',after.material_bytes);
      }
      await openTool();
      await page.evaluate(()=>{
        window.cfFrames=[];window.cfObserver=setInterval(()=>{
          const visible=e=>!!e.getClientRects().length;
          const supplements=[...document.querySelectorAll('textarea[aria-label="补充说明"]')].filter(visible);
          const submits=[...document.querySelectorAll('button')].filter(visible).filter(e=>/^(帮我看看|重新估算)$/.test(e.innerText.trim()));
          window.cfFrames.push({supplements:supplements.length,submits:submits.length,values:supplements.map(e=>e.value),thinking:document.body.innerText.includes('THINKING.......')});
        },30);
      });
      await button('帮我看看').click();await page.getByText('THINKING.......',{exact:true}).waitFor();await shot('14-'+story+'-thinking');await idle();
      const frames=await page.evaluate(()=>{clearInterval(window.cfObserver);return window.cfFrames;});
      assert(frames.every(f=>f.supplements<=1&&f.submits<=1),'Duplicate material inputs');
      const text=await (await openTool()).innerText();
      if(story==='needs_input')assert(text.includes('你准备怎么处理')&&!text.includes('cf-estimate-metric'),'Reference fabricated action');
      else assert(await page.locator('.cf-material-estimate').count()===1,'Estimate missing');
      if(story==='workload_partial'||story==='workload_empty')assert(text.includes('可能需要更久'),'Partial coverage overclaims');
      if(story==='workload_empty')assert(text.includes('22–76')&&text.includes('49'),'Local workload estimate lost');
      await shot('15-'+story+'-result-1440');
      record(story,{frames:frames.length,maxSupplement:Math.max(...frames.map(f=>f.supplements)),maxSubmit:Math.max(...frames.map(f=>f.submits))});
      if(story==='workload_partial') {await page.setViewportSize({width:390,height:844});await shot('16-material-partial-390');await noOverflow();}
      await page.context().close();
    }
    record('complete');return {out,results};
  } catch(error) {
    if(page&&!page.isClosed()){await page.screenshot({path:path.join(out,'failure.png')});record('failure text',(await page.locator('body').innerText()).slice(-7000));}
    record('failure',String(error));throw error;
  }
};
