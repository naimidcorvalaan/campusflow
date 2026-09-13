/* Two independent browser contexts exercising profile isolation on a temp DB. */
const {chromium} = require('playwright');
const fs = require('fs'), path = require('path');
const output = path.resolve('artifacts/nightly_ui_preview');
const assert = (ok,msg) => { if (!ok) throw Error(msg); };

async function main() {
  const url = process.env.CAMPUSFLOW_PREVIEW_URL || 'http://127.0.0.1:8513?profile=profiles&identity=anonymous';
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+[/?]/.test(url)) throw Error('Local preview only');
  const browser = await chromium.launch({headless:true, executablePath:process.env.CAMPUSFLOW_BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  try {
  const contextA = await browser.newContext({viewport:{width:1440,height:900}});
  const contextB = await browser.newContext({viewport:{width:1024,height:768}});
  const pageA = await contextA.newPage(), pageB = await contextB.newPage();
  const wait = page => page.waitForTimeout(900);
  const calls = async page => Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：(\d+)/)[1]);
  const openProfile = async (page, id, captureLegacyPrompt = false) => {
    await page.getByRole('button',{name:'个人设置',exact:true}).click();
    await page.getByText('个人档案',{exact:true}).waitFor();
    await page.getByRole('checkbox',{name:'开启个性化',exact:true}).evaluate(element => element.click()); await wait(page);
    if (captureLegacyPrompt) {
      await page.getByText('将升级前的本机档案复制到这个个人档案',{exact:true}).waitFor();
      await page.screenshot({path:path.join(output,'profiles-legacy-import-1440.png')});
    }
    await page.getByRole('textbox',{name:'学号',exact:true}).fill(id);
    await page.getByRole('button',{name:'打开并使用此档案',exact:true}).click();
    await page.getByText(/个人档案已启用/).waitFor(); await wait(page);
  };
  const note = page => page.getByRole('textbox',{name:'还有什么希望 CampusFlow 平时记住？',exact:true});
  fs.mkdirSync(output,{recursive:true});
  await Promise.all([pageA.goto(url),pageB.goto(url)]); await Promise.all([wait(pageA),wait(pageB)]);
  assert(await calls(pageA)===0 && await calls(pageB)===0,'Opening anonymous sessions called model');
  await openProfile(pageA,'3026200001',true);
  await note(pageA).fill('A档案只保留这句话'); await pageA.getByRole('button',{name:'保存设置',exact:true}).click(); await wait(pageA);
  await openProfile(pageB,'3026200002');
  assert(await note(pageB).inputValue()==='','B saw A settings');
  await note(pageB).fill('B档案只保留这句话'); await pageB.getByRole('button',{name:'保存设置',exact:true}).click(); await wait(pageB);
  const contextC = await browser.newContext({viewport:{width:390,height:844}}), pageC = await contextC.newPage();
  await pageC.goto(url); await wait(pageC); await openProfile(pageC,'3026200001');
  assert(await note(pageC).inputValue()==='A档案只保留这句话','A did not restore in a fresh browser context');
  assert(await calls(pageA)===0 && await calls(pageB)===0 && await calls(pageC)===0,'Profile actions called model');
  await pageA.screenshot({path:path.join(output,'profiles-a-1440.png')});
  await pageB.screenshot({path:path.join(output,'profiles-b-1024.png')});
  await pageC.screenshot({path:path.join(output,'profiles-a-restored-390.png')});
  console.log(JSON.stringify({status:'completed',modelCalls:[await calls(pageA),await calls(pageB),await calls(pageC)]}));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode=1; });
