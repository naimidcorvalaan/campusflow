/* Real Streamlit UI, synthetic blank Word form + bounded offline responses. */
const fs = require('fs'), path = require('path');
module.exports = async function check(chromium, base='http://127.0.0.1:8571/', cases=[
    ['workload','',1440],['workload','填表',1440],['workload_partial','填表',390],['needs_input','',1440]]) {
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(base)) throw Error('Local preview only');
  const root=path.resolve('artifacts/document_material'), out=path.join(root,'workload','browser-'+Date.now());
  fs.mkdirSync(out,{recursive:true});
  const assert=(value,message)=>{if(!value)throw Error(message);};
  const results=[];
  for (const [story,supplement,width] of cases) {
    const tag=story+'-'+(supplement?'with-intent':'upload')+'-'+width;
    const context=await chromium.launchPersistentContext(path.join(out,'profile-'+tag),{
      headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
      viewport:{width,height:width===390?844:1000}});
    const page=await context.newPage();
    try {
      const button=name=>page.getByRole('button',{name,exact:true});
      const idle=async()=>{await page.locator('[data-testid="stStatusWidget"]').waitFor({state:'hidden'}); await page.waitForTimeout(250);};
      await page.goto(base+'?profile=release&identity=anonymous&documents=1&estimate_rehearsal='+story);
      await button('个人设置').waitFor();await idle();
      const area=page.locator('[data-testid="stExpander"]').filter({has:page.locator('.cf-add-task-marker')});
      if(!await area.locator('details').evaluate(e=>e.open))
        await page.getByText('难以估计任务时间？让campusflow帮你估',{exact:true}).click();
      const file=story==='exam'?path.join(root,'exam.pdf'):
        ['full','fallback'].includes(story)?path.join(root,'application.docx'):
        story==='needs_input'?path.join(root,'workload','reference.docx'):
        path.join(root,'workload',story==='workload_partial'||width===390?'assessment-partial.docx':'assessment.docx');
      await page.locator('input[type=file]').setInputFiles(!story.startsWith('workload')?file:{
        name:'本科生综合素质测评考核表【主观评价部分】.docx',
        mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer:fs.readFileSync(file)});
      await area.getByText(/已读取材料/).waitFor();await idle();
      if (supplement) {
        await button('补充我的情况').click();
        await area.getByRole('textbox',{name:'补充说明',exact:true}).fill(supplement);
      }
      const snapshot=async suffix=>{
        await area.evaluate(el=>{document.querySelector('section.main').scrollTop+=el.getBoundingClientRect().top-70;});
        await page.screenshot({path:path.join(out,tag+'-'+suffix+'.png')});
      };
      async function submit(label,round) {
        await snapshot(round+'-before');
        await page.evaluate(()=>{
          window.cfSamples=[]; window.cfSampling=true;
          const sample=()=>{
            if(!window.cfSampling)return;
            const marker=document.querySelector('.cf-add-task-marker');
            const area=marker?.closest('[data-testid="stExpander"]');
            if(area) {
              const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
              const texts=[...area.querySelectorAll('textarea')].filter(visible);
              const submits=[...area.querySelectorAll('button')].filter(visible).filter(e=>/^(帮我看看|重新估算)$/.test(e.innerText.trim()));
              if(texts.length>1&&!window.cfDuplicateDOM)window.cfDuplicateDOM=texts.map(e=>{
                const parents=[];for(let p=e;p&&p!==area;p=p.parentElement)parents.push({tag:p.tagName,testid:p.getAttribute('data-testid'),stale:p.getAttribute('data-stale'),class:p.className});
                return {id:e.id,parents};
              });
              window.cfSamples.push({supplements:texts.filter(e=>e.getAttribute('aria-label')==='补充说明').length,
                submits:submits.length,values:texts.map(e=>e.value),disabled:texts.map(e=>e.disabled),
                thinking:area.innerText.includes('THINKING.......')});
            }
            requestAnimationFrame(sample);
          }; requestAnimationFrame(sample);
        });
        await button(label).click();
        const spinner=page.getByText('THINKING.......',{exact:true});
        await spinner.waitFor();await snapshot(round+'-thinking');
        await spinner.waitFor({state:'hidden',timeout:30000});await idle();
        const samples=await page.evaluate(()=>{window.cfSampling=false;return window.cfSamples;});
        fs.writeFileSync(path.join(out,tag+'-'+round+'-frames.json'),JSON.stringify(samples));
        const duplicateDOM=await page.evaluate(()=>window.cfDuplicateDOM);
        if(duplicateDOM)fs.writeFileSync(path.join(out,tag+'-duplicate-dom.json'),JSON.stringify(duplicateDOM,null,2));
        assert(samples.some(s=>s.thinking),'No processing observation');
        assert(samples.every(s=>s.supplements<=1&&s.submits<=1),'Duplicate input controls: '+JSON.stringify(samples.filter(s=>s.supplements>1||s.submits>1).slice(0,3)));
        if(supplement)assert(samples.filter(s=>s.thinking).every(s=>s.values.length===1&&s.values[0]===supplement&&s.disabled[0]),'Lost or editable processing supplement: '+JSON.stringify(samples.filter(s=>s.thinking&&(s.values.length!==1||s.values[0]!==supplement||!s.disabled[0])).slice(0,5)));
        await snapshot(round+'-after');
        results.push({tag,round,frames:samples.length,thinkingFrames:samples.filter(s=>s.thinking).length,
          maxSupplement:Math.max(...samples.map(s=>s.supplements)),maxSubmit:Math.max(...samples.map(s=>s.submits))});
      }
      await submit('帮我看看','initial');
      const text=await area.innerText();
      if(story==='needs_input') {
        assert(text.includes('你准备怎么处理')&&!text.includes('35–65'),'Reference invented time');
      } else if (story.startsWith('workload')) {
        assert(await area.locator('.cf-material-estimate').count()===1,'Missing/duplicate estimate');
        if(story==='workload_empty') {
          // Two basic fields, five scores, 300-word self-evaluation, recall,
          // one proof-name entry, preparing proof, and review: 22–76, midpoint 49.
          assert(text.includes('22–76')&&text.includes('49'),'Feature-based local range missing');
          assert(text.includes('2个基础字段')&&text.includes('5个选择或评分项')&&text.includes('300字'),'Workload features missing');
          assert(text.includes('先估算')&&text.includes('保守粗估'),'Rough estimate not identified');
          assert(!/未能生成|无法估|估时未完成/.test(text),'Internal failure leaked into actionable form');
        } else if(story==='workload_model')assert(text.includes('43'),'Short independent estimate missing');
        else assert(text.includes('35–65')&&text.includes('50')&&text.includes('300字'),'Missing workload/time/basis');
        assert(!text.includes('你准备怎么处理'),'Repeated intent question');
        assert(text.includes('外部等待不计入专注用时'),'Waiting counted as focus');
        if(['workload_partial','workload_empty','workload_model'].includes(story))assert(text.includes('可能需要更久'),'Partial misrepresented as whole');
        else assert(text.includes('预计专注用时')&&!text.includes('可能需要更久'),'Fully readable controls reported unread');
        assert(!/parser|schema|formal items|task identity/.test(text),'Engineering copy');
        if(supplement) {
          assert(await area.getByRole('textbox',{name:'补充说明',exact:true}).inputValue()===supplement,'Lost submitted intent');
          await submit('重新估算','retry');
          if(story==='workload'&&width===1440) {
            await page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true}).click();
            await page.locator('[data-testid="stSelectbox"]').filter({hasText:'演示服务状态'}).getByRole('combobox').click();
            await page.getByRole('option',{name:'模拟网络失败',exact:true}).click();await idle();
            await submit('重新估算','failed-retry');
            assert((await area.innerText()).includes('35–65'),'Failed retry erased estimate');
            assert(await area.getByRole('textbox',{name:'补充说明',exact:true}).inputValue()===supplement,'Failure lost intent');
          }
        } else assert(await area.locator('textarea:visible').count()===0,'Large default editor');
      } else {
        const expected=story==='exam'?'120–180':'15–30';
        assert((await area.innerText()).includes(expected),'Existing document estimate lost');
        if(story==='fallback') {
          await button('加入计划').click();
          await page.getByText('这是新任务，不是固定安排，没有截止时间或指定执行地点',{exact:true}).click();await idle();
          await button('按20分钟准备加入').click();await button('核对并加入计划').waitFor();await idle();
        }
        if(story!=='exam') {
          await button('核对并加入计划').click();await button('确认并加入计划').waitFor();await idle();
          await button('确认并加入计划').click();
          await page.getByText('这份材料已确认并安排。修改上方材料可整理下一份。',{exact:true}).waitFor({state:'attached',timeout:30000});await idle();
          results.push({tag,confirmed:true});
        }
      }
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow');
      await page.getByText('开发演示 · synthetic demo · 不连接真实模型',{exact:true}).click();
      const calls=Number((await page.getByText(/本次浏览器会话的 mock 调用：/).textContent()).match(/调用：\s*(\d+)/)[1]);
      if(story.startsWith('workload')||story==='needs_input')assert(calls<= (supplement?4:2),'Unbounded calls');
      if(story==='workload_empty'||story==='workload_model') {
        const requests=JSON.parse(await page.locator('pre').last().innerText());
        assert(requests.length===calls,'Missing actual request trace');
        assert(requests.every(r=>r.supplement===(supplement||null)&&r.feature_count>0),'Intent/features lost before model');
        assert(requests.filter(r=>r.stage==='workload_estimate').every(r=>r.no_formal_fields),'Formal schema leaked into estimate-only call');
        results.push({tag,requests});
      }
      results.push({tag,calls,text});
    } catch(error) {
      await page.screenshot({path:path.join(out,tag+'-failure.png')});
      fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:String(error),results},null,2));
      throw error;
    } finally {await context.close();}
  }
  fs.writeFileSync(path.join(out,'observations.json'),JSON.stringify(results,null,2));
  return {out,results};
};
if(require.main===module)module.exports(require('playwright').chromium,process.env.CAMPUSFLOW_PREVIEW_URL)
  .then(result=>console.log(JSON.stringify(result))).catch(error=>{console.error(error);process.exitCode=1;});
