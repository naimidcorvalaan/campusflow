/* Actual native-widget rehearsal. Existing Playwright + Edge; no installation. */
const {chromium} = require('playwright');
const fs = require('fs'), path = require('path');
const output = path.resolve('artifacts/nightly_ui_preview');
const story = '在图书馆写90分钟作业，然后背60分钟单词，可以拆开。19:00去9教上课，上一小时半，上课前吃晚饭。';
const whatif = '如果改成先背单词，再写作业，会怎么安排？先给我看看，暂时不要改。';
const assert = (ok,msg) => {if (!ok) throw Error(msg);};
let browser, page;
async function main() {
  const url = process.env.CAMPUSFLOW_PREVIEW_URL || 'http://127.0.0.1:8510?profile=round2';
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+[/?]/.test(url)) throw Error('Local preview only');
  const prefix = new URL(url).searchParams.get('profile') || 'demo';
  browser = await chromium.launch({headless:true,executablePath:process.env.CAMPUSFLOW_BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  page = await browser.newPage({viewport:{width:1440,height:900}});
  fs.mkdirSync(output,{recursive:true});
  const observations = [], wait = () => page.waitForTimeout(1300);
  const button = name => page.getByRole('button',{name,exact:true});
  const input = name => page.getByRole('textbox',{name,exact:true});
  const count = async () => Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：(\d+)/)[1]);
  const shot = async name => {await page.waitForTimeout(500);await page.screenshot({path:path.join(output,`${prefix}-${name}.png`)});};
  const align = async locator => locator.evaluate(el => {
    const area = el.closest('[role="dialog"]') || document.querySelector('section.main');
    area.scrollTop += el.getBoundingClientRect().top-area.getBoundingClientRect().top-90;
  });
  const metrics = async name => {
    const value = await page.evaluate(()=>({viewport:{width:innerWidth,height:innerHeight},scrollWidth:document.documentElement.scrollWidth,
      cards:[...document.querySelectorAll('.cf-timeline-card,.cf-side-card')].map(e=>e.getBoundingClientRect().toJSON()),
      dialog:[...document.querySelectorAll('[role="dialog"]')].map(e=>({rect:e.getBoundingClientRect().toJSON(),scrollHeight:e.scrollHeight,clientHeight:e.clientHeight}))}));
    assert(value.scrollWidth<=value.viewport.width,'Horizontal overflow '+name);
    if(value.viewport.width>800 && value.cards.length===2) assert(Math.abs(value.cards[0].top-value.cards[1].top)<2 && Math.abs(value.cards[0].bottom-value.cards[1].bottom)<2,'Unequal cards '+name);
    observations.push({name,...value});
  };
  const courseTab = () => page.getByRole('tab',{name:'我的课表',exact:true}).click();
  await page.goto(url); await button('个人设置').waitFor(); await wait();
  assert(await count()===0,'First open called model');
  if (!process.env.CAMPUSFLOW_RESUME_AFTER_SETTINGS) {
  await shot('first-use-1440');
  // Second walk begins in a fresh profile, with settings before any plan.
  await button('个人设置').click(); await page.getByRole('dialog',{name:'个人设置'}).waitFor();
  await input('还有什么希望 CampusFlow 平时记住？').fill('我做数学可能需要查笔记。'); await input('还有什么希望 CampusFlow 平时记住？').press('Tab'); await wait();
  await shot('settings-1440');
  const drawer=page.getByRole('dialog',{name:'个人设置'});
  await page.getByRole('tab',{name:'常用地点',exact:true}).click(); await wait();
  const placeText=await drawer.innerText();
  assert(placeText.includes('校区') && placeText.includes('宿舍') && placeText.includes('常用学习地点'),'Saved-place labels missing');
  assert(!placeText.includes('地图锚点'),'Engineering place term leaked');
  await shot('places-1440');
  await courseTab(); await page.getByText('启用手动课表',{exact:true}).click(); await wait(); await courseTab();
  for(const [index,value] of [[0,'2026/08/31'],[1,'2027/01/17']]) {
    const date=page.locator('[data-testid="stDateInput"] input').nth(index);
    await date.fill(value); await date.press('Enter'); await wait();
    await date.press('Escape');
    assert(await page.getByRole('dialog',{name:'个人设置'}).count()===1,'Calendar Escape closed the drawer');
    assert(await page.getByRole('tab',{name:'我的课表',exact:true}).getAttribute('aria-selected')==='true','Editing date reset active settings area');
  }
  await input('课表文字').fill('大学英语，周二10:00–11:30，第1–16周，卫津路校区，第九教学楼');
  assert(await count()===0,'Editing settings called model');
  await button('识别课表').click(); await page.getByText('识别预览',{exact:true}).waitFor(); await wait();
  assert(await count()===1,'Import budget');
  for(const [width,height] of [[1440,900],[1024,768],[820,1180],[390,844]]) {
    await page.setViewportSize({width,height}); await align(page.getByText('识别预览',{exact:true}));
    await shot(`import-${width}`); await metrics(`import-${width}`);
  }
  await button('确认导入并保存设置').click(); await wait(); assert(await count()===1,'Import confirmation called model');
  assert(await page.getByRole('tab',{name:'我的课表',exact:true}).getAttribute('aria-selected')==='true','Import reset active settings area');
  await page.getByRole('button',{name:/^(关闭个人设置|×)$/}).click(); await wait(); await button('个人设置').click();
  await page.getByRole('tab',{name:'我的偏好',exact:true}).click();
  await input('还有什么希望 CampusFlow 平时记住？').waitFor(); assert(await input('还有什么希望 CampusFlow 平时记住？').inputValue()==='我做数学可能需要查笔记。','Preference missing');
  await page.keyboard.press('Escape'); await wait();
  assert(await page.getByRole('dialog',{name:'个人设置'}).count()===0,'Escape failed');
  assert(await button('个人设置').evaluate(e=>e===document.activeElement),'Focus did not return');
  } else {
    await button('个人设置').click(); await page.getByRole('dialog',{name:'个人设置'}).waitFor();
    await page.getByRole('tab',{name:'我的课表',exact:true}).click(); await wait();
    await page.keyboard.press('Escape'); await wait();
    assert(await page.getByRole('dialog').count()===0,'Escape failed after restart');
    assert(await button('个人设置').evaluate(e=>e===document.activeElement),'Focus did not return after restart');
    assert(await count()===0,'Settings reopen called model');
  }
  await page.setViewportSize({width:1440,height:900});
  await page.getByRole('combobox').first().click(); await page.getByRole('option',{name:'卫津路校区',exact:true}).click(); await wait();
  await input('接下来想做什么？').fill(story); await shot('input-1440'); await button('帮我安排').click();
  await page.locator('.cf-timeline-card').waitFor(); await wait();
  await align(page.locator('.cf-plan-hero')); await shot('plan-1440'); await metrics('long-plan-1440');
  const timeline=page.locator('.cf-timeline-card');
  assert((await timeline.innerText()).includes('40 分钟') && (await timeline.innerText()).includes('第九教学楼'),'Meal or route missing');
  assert((await timeline.innerText()).includes('默认按步行计算'),'First default walk hint missing');
  await page.getByText('不知道任务要多久？让 CampusFlow 帮你估',{exact:true}).click();
  await input('任务文字').fill('完成高数作业第3—6题，保留步骤并检查；前两题已经做完。');
  await button('帮我估时').click(); await page.locator('.cf-estimate-result').waitFor(); await wait();
  await align(page.locator('.cf-estimate-result')); await shot('estimate-1440');
  await page.setViewportSize({width:390,height:844}); await align(page.locator('.cf-estimate-result')); await shot('estimate-390'); await metrics('estimate-390');
  const estimateCalls=await count();
  const minutes=page.getByRole('spinbutton',{name:'采用的分钟数',exact:true});
  await minutes.fill('30'); await minutes.press('Tab'); await wait(); assert(await count()===estimateCalls,'Minutes edit called model');
  await button('加入今日计划').click(); await wait(); await page.getByText(/已加入今日计划。正式任务已保存/).waitFor();
  assert(await button('加入今日计划').count()===0,'Duplicate add still available');
  await page.getByText('不知道任务要多久？让 CampusFlow 帮你估',{exact:true}).click(); await page.setViewportSize({width:1440,height:900});
  const prior=await timeline.innerText();
  await input('告诉 CampusFlow 发生了什么……').fill(whatif); await button('更新方案').click(); await button('采用这个调整').waitFor(); await wait();
  assert(await timeline.innerText()===prior,'Preview changed formal plan'); await align(page.locator('.cf-whatif-card')); await shot('whatif-1440');
  const previewCalls=await count(); await button('保持现在方案').click(); await wait();
  assert(await count()===previewCalls && await timeline.innerText()===prior,'Keep mutated plan/called model');
  await button('更新方案').click(); await button('采用这个调整').waitFor(); await wait();
  const adoptCalls=await count(); await button('采用这个调整').click(); await wait(); assert(await count()===adoptCalls,'Adopt called model');
  const adopted=await timeline.innerText(); assert(adopted.indexOf('背单词')<adopted.indexOf('写作业'),'Wrong adopted order');
  await page.reload(); await page.getByText('已恢复上次记录。',{exact:true}).waitFor(); await wait();
  assert(await count()===0,'Restore called model'); assert(await timeline.innerText()===adopted,'Restored plan differs');
  assert(!(await timeline.innerText()).includes('默认按步行计算'),'Default walk hint repeated after reopen');
  for(const [width,height] of [[1440,900],[1024,768],[820,1180],[390,844]]) {
    await page.setViewportSize({width,height}); await align(page.locator('.cf-plan-hero')); await shot(`restored-plan-${width}`); await metrics(`restored-plan-${width}`);
  }
  fs.writeFileSync(path.join(output,`${prefix}-measurements.json`),JSON.stringify(observations,null,2));
  console.log(JSON.stringify({status:'completed',journey:prefix,observations:observations.map(o=>o.name),adoptCalls,restoredCalls:await count()}));
  await browser.close();
}
async function polish() {
  browser = await chromium.launch({headless:true,executablePath:process.env.CAMPUSFLOW_BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  page = await browser.newPage({viewport:{width:1440,height:900}});
  fs.mkdirSync(output,{recursive:true});
  const wait=()=>page.waitForTimeout(1400), button=name=>page.getByRole('button',{name,exact:true});
  const input=name=>page.getByRole('textbox',{name,exact:true});
  const shot=async name=>{await page.waitForTimeout(500);await page.screenshot({path:path.join(output,`polish-${name}.png`)});};
  const align=async locator=>locator.evaluate(el=>{const area=el.closest('[role="dialog"]')||document.querySelector('section.main');area.scrollTop+=el.getBoundingClientRect().top-area.getBoundingClientRect().top-100;});
  const count=async()=>Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：(\d+)/)[1]);
  const records=[];
  await page.goto('http://127.0.0.1:8510?profile=short');await button('个人设置').waitFor();await wait();
  await page.setViewportSize({width:390,height:844});await shot('first-use-390');await page.setViewportSize({width:1440,height:900});
  await page.getByRole('combobox').first().click();await page.getByRole('option',{name:'卫津路校区',exact:true}).click();await wait();
  await page.getByText('不知道任务要多久？让 CampusFlow 帮你估',{exact:true}).click();
  await input('任务文字').fill('完成高数作业第3—6题，保留步骤并检查；前两题已经做完。');
  await button('帮我估时').click();await page.locator('.cf-estimate-result').waitFor();await wait();
  await input('有什么会影响你完成速度？（可选）').fill('知道大概方法，可能需要查笔记。');await input('有什么会影响你完成速度？（可选）').press('Tab');await wait();
  assert(await button('加入今日计划').isDisabled(),'Changed draft can be added');
  const editCalls=await count();await button('重新估算').click();await wait();assert(await count()===editCalls+1,'Re-estimate budget');
  await page.getByRole('spinbutton',{name:'采用的分钟数',exact:true}).fill('30');await button('加入今日计划').click();await wait();
  await page.locator('.cf-timeline-card').waitFor();assert((await page.locator('.cf-timeline-card').innerText()).includes('14:30'),'Empty-plan estimate did not create first task');
  await page.getByText('不知道任务要多久？让 CampusFlow 帮你估',{exact:true}).click();
  await align(page.locator('.cf-plan-hero'));await shot('short-plan-1440');
  records.push({name:'short-plan',cards:await page.locator('.cf-timeline-card,.cf-side-card').evaluateAll(es=>es.map(e=>e.getBoundingClientRect().toJSON()))});
  await page.reload();await page.getByText('已恢复上次记录。',{exact:true}).waitFor();await wait();assert(await count()===0,'Short-plan restore called model');
  await page.getByText('不知道任务要多久？让 CampusFlow 帮你估',{exact:true}).click();await page.getByText(/已加入今日计划。正式任务已保存/).waitFor();
  assert(await button('加入今日计划').count()===0,'Restore re-enabled added draft');

  await page.goto('http://127.0.0.1:8510?profile=round2&story=restored');await page.locator('.cf-plan-hero').waitFor();await wait();
  assert(await count()===0,'Stale restore called model');
  assert(!(await page.locator('.cf-timeline-card').innerText()).includes('现在'),'Stale plan claims current action');
  await page.evaluate(()=>document.querySelector('section.main').scrollTop=0);await shot('stale-restoration-1440');
  await button('个人设置').click();await page.getByRole('dialog').waitFor();await page.getByRole('tab',{name:'我的课表',exact:true}).click();
  for(const [width,height] of [[1440,900],[820,1180],[390,844]]) {
    await page.setViewportSize({width,height});await page.getByRole('dialog').evaluate(e=>e.scrollTop=e.scrollHeight);await wait();
    const rect=await page.getByRole('button',{name:/^(关闭个人设置|×)$/}).boundingBox();
    records.push({name:`scrolled-settings-${width}`,close:rect});assert(rect.y>=0 && rect.y+rect.height<=height,'Close button hidden after scroll');
    await shot(`settings-scrolled-${width}`);
  }
  await page.keyboard.press('Escape');await wait();assert(await page.getByRole('dialog').count()===0,'Escape failed');
  await page.setViewportSize({width:1440,height:900});
  await page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true}).click();
  await page.getByRole('combobox',{name:/演示服务状态/}).click();await page.getByRole('option',{name:'模拟网络失败',exact:true}).click();await wait();
  await page.getByText('不知道任务要多久？让 CampusFlow 帮你估',{exact:true}).click();
  await button('重新估算').click();await wait();
  await align(page.getByText(/估时服务暂时没有完成/));await shot('estimate-network-failure-1440');
  assert(await page.locator('.cf-timeline-card').count()===1,'Network failure removed reliable plan');
  await page.getByRole('combobox',{name:/演示服务状态/}).click();await page.getByRole('option',{name:'模拟格式错误',exact:true}).click();await wait();
  await button('重新估算').click();await wait();await align(page.getByText('暂时没能生成可靠估算，请重试。',{exact:true}));await shot('estimate-format-failure-1440');
  assert(await page.locator('.cf-timeline-card').count()===1,'Format failure removed plan');
  fs.writeFileSync(path.join(output,'polish-measurements.json'),JSON.stringify(records,null,2));
  console.log(JSON.stringify({status:'completed',checks:records,staleRestoredWithoutCalls:true}));await browser.close();
}
(process.env.CAMPUSFLOW_CHECK_MODE==='polish'?polish():main()).catch(async error=>{
  console.error(error.message);
  if(page){await page.screenshot({path:path.join(output,'browser-failure.png')}).catch(()=>{});console.error((await page.locator('body').innerText()).slice(0,2500));}
  if(browser) await browser.close();process.exitCode=1;
});
