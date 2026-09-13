/* Run through the installed Playwright runtime; no dependencies to install. */
const fs=require('fs'),path=require('path');
module.exports=async function check(chromium,base='http://127.0.0.1:8576/'){
 if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(base))throw Error('Local prototype only');
 const out=path.join(__dirname,'screenshots');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(7000);
 const errors=[],checks=[],remote=[];page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(!r.url().startsWith(base))remote.push(r.url())});
 const assert=(ok,label)=>{if(!ok)throw Error(label);checks.push(label)};
 const scene=async name=>{await page.locator('[data-scene="'+name+'"]').click();await page.mouse.move(1430,880);await page.waitForTimeout(220)};
 try{
  await page.goto(base);
  for(const name of ['empty','today','timeline','material','settings','touch']){
   await scene(name);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No 1440 overflow: '+name);
   await page.screenshot({path:path.join(out,name+'-1440.png')});
   if(name==='timeline')assert(await page.locator('.time-row').count()===9,'Timeline includes all nine intervals');
  }
  await scene('empty');await page.locator('#fill-example').click();assert((await page.locator('#plan-form textarea').inputValue()).includes('90分钟'),'Example fills composer');
  await page.locator('#plan-form .primary').click();await page.locator('.current-action').waitFor();assert(await page.locator('.current-action h2').innerText()==='写作业','Mock plan opens current action');
  await page.getByRole('button',{name:'更新进度',exact:false}).click();await page.locator('#feedback-text').fill('作业还剩40分钟');await page.locator('#apply-feedback').click();assert(!await page.locator('#detail-dialog').evaluate(e=>e.open),'Feedback dialog closes after mock submission');
  await scene('material');await page.locator('#toggle-supplement').click();await page.locator('#supplement-text').fill('填表，证明已经准备好了');await page.locator('#toggle-supplement').click();await page.locator('#toggle-supplement').click();assert(await page.locator('#supplement-text').inputValue()==='填表，证明已经准备好了','Disclosure retains supplement');
  await page.locator('#reestimate').click();assert(await page.locator('#supplement-text').isDisabled(),'Supplement disabled during mock processing');await page.waitForTimeout(850);assert(await page.locator('#supplement-text').inputValue()==='填表，证明已经准备好了','Reestimate retains supplement');
  await page.screenshot({path:path.join(out,'material-expanded-1440.png')});
  await scene('today');await page.locator('.rail-setting').click();assert(await page.locator('#close-settings').evaluate(e=>document.activeElement===e),'Drawer focuses close');await page.keyboard.press('Shift+Tab');assert(await page.locator('#save-settings').evaluate(e=>document.activeElement===e),'Drawer traps focus');
  await page.locator('#settings-note').fill('学习一段时间后休息一下');await page.locator('[data-setting-tab="places"]').click();await page.locator('[data-setting-tab="preferences"]').click();assert(await page.locator('#settings-note').inputValue()==='学习一段时间后休息一下','Settings draft survives group switch');
  await page.locator('#save-settings').click();await page.keyboard.press('Escape');assert(await page.locator('.rail-setting').evaluate(e=>e===document.activeElement),'Escape restores trigger focus');await page.locator('.rail-setting').click();assert(await page.locator('#settings-note').inputValue()==='学习一段时间后休息一下','Saved prototype settings retained in memory');await page.keyboard.press('Escape');
  await scene('touch');const style=el=>el.evaluate(e=>({bg:getComputedStyle(e).backgroundColor,transform:getComputedStyle(e).transform,shadow:getComputedStyle(e).boxShadow,active:e.matches(':active')}));const button=page.locator('#touch-primary');await page.mouse.move(900,700);await page.waitForTimeout(180);const normal=await style(button);await button.hover();await page.waitForTimeout(200);const hover=await style(button);await page.screenshot({path:path.join(out,'touch-hover-1440.png')});await page.mouse.down();await page.waitForTimeout(220);const pressed=await style(button);await page.screenshot({path:path.join(out,'touch-pressed-1440.png')});await page.mouse.up();await page.waitForTimeout(220);const released=await style(button);
  assert(pressed.active&&pressed.transform.includes('0.975')&&pressed.shadow.includes('inset'),'Real mouse pressed state shrinks and insets');assert(normal.bg!==hover.bg&&hover.bg!==pressed.bg,'Hover and pressed colors differ');assert(released.transform==='none'&&!released.active,'Release returns to resting geometry');
  const row=page.locator('#touch-row');await row.hover();await page.mouse.down();await page.waitForTimeout(220);const rowPressed=await style(row);await page.screenshot({path:path.join(out,'row-pressed-1440.png')});await page.mouse.up();assert(rowPressed.active&&rowPressed.transform.includes('0.992'),'Clickable row has restrained real pressed state');
  await page.emulateMedia({reducedMotion:'reduce'});await button.hover();await page.mouse.down();await page.waitForTimeout(100);assert((await style(button)).transform==='none','Reduced motion removes movement');await page.mouse.up();await page.emulateMedia({reducedMotion:'no-preference'});
  for(const name of ['today','material','settings']){await page.setViewportSize({width:390,height:844});await scene(name);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No narrow overflow: '+name);await page.screenshot({path:path.join(out,name+'-390.png')})}
  assert(errors.length===0,'No JavaScript errors');assert(remote.length===0,'No external requests');fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify({checks,errors,remote,normal,hover,pressed,released,rowPressed},null,2));return {checks:checks.length,out,errors};
 }catch(e){await page.screenshot({path:path.join(out,'check-failure.png')});throw e}finally{await browser.close()}
};
