/* Local synthetic browser evidence; reuses the installed development Playwright. */
const {chromium} = require('playwright');
const fs = require('fs'), path = require('path');
const output = path.resolve('artifacts/low_input_preview');
const assert = (ok, message) => { if (!ok) throw Error(message); };
(async () => {
  const browser = await chromium.launch({headless:true,
    executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  fs.mkdirSync(output,{recursive:true});
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  const button = name => page.getByRole('button',{name,exact:true});
  const input = name => page.getByRole('textbox',{name,exact:true});
  const count = async () => Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：(\d+)/)[1]);
  const observations = [];
  const capture = async name => {
    await page.waitForTimeout(500);
    const metrics = await page.evaluate(()=>({width:innerWidth,height:innerHeight,
      scrollWidth:document.documentElement.scrollWidth}));
    assert(metrics.width >= metrics.scrollWidth, 'Horizontal overflow');
    observations.push({name,...metrics,calls:await count()});
    await page.screenshot({path:path.join(output,name+'.png')});
  };
  try {
    await page.goto('http://127.0.0.1:8541/?identity=anonymous&low_input=b');
    await input('接下来想做什么？').waitFor();
    assert(await count() === 0, 'First open called model');
    await capture('first-1440');
    await input('接下来想做什么？').fill('想整理一下今天的笔记。');
    await button('帮我安排').click();
    await page.getByText('AI暂估',{exact:true}).waitFor();
    await capture('plan-1440');
    const plannedCalls = await count();
    await page.setViewportSize({width:390,height:844});
    await input('告诉 CampusFlow 发生了什么……').scrollIntoViewIfNeeded();
    await capture('feedback-390');
    assert(await count() === plannedCalls, 'Resize called model');
    await button('个人设置').click();
    await page.getByRole('dialog',{name:'个人设置'}).waitFor();
    await capture('settings-390');
    await page.keyboard.press('Escape');
    assert(await count() === plannedCalls, 'Settings called model');
    await page.setViewportSize({width:820,height:1180});
    await capture('plan-820');
    await page.goto('http://127.0.0.1:8541/?identity=anonymous&low_input=b&unconfigured=1');
    await page.getByText('本机模型服务尚未配置；可以先查看页面和编辑设置，配置后再生成计划。',{exact:true}).waitFor();
    assert(await count() === 0, 'Missing configuration called model');
    await capture('unconfigured-820');
    fs.writeFileSync(path.join(output,'observations.json'),JSON.stringify(observations,null,2));
    console.log(JSON.stringify(observations));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
