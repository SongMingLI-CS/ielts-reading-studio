/** Runnable with a CUA Tab and viewport capability. All page evaluation is DOM read-only.
 * Uses real controls, wheel scrolling, reload and server save; no injected page state.
 * Start on the isolated 12-question Lost Tomb article. Exam must already be started.
 */
import assert from 'node:assert/strict';

export async function sessionRegression(tab, viewport) {
  const evidence = [];
  const state = async () => { await tab.getAXState({emit:false}); };
  const read = () => tab.playwright.evaluate(() => ({
    current: document.querySelector('#numbers [aria-current="step"]')?.id,
    focus: document.activeElement?.name,
    passage: document.querySelector('.passage-scroll').scrollTop,
    questions: document.querySelector('.questions-scroll').scrollTop,
    q6: [...document.querySelectorAll('[name=q6]')].find(e=>e.checked)?.value,
    flag6: document.querySelector('[data-flag="6"]').getAttribute('aria-pressed'),
    status: document.getElementById('autosave-status').textContent
  }));
  await viewport.set({width:1280,height:800});
  await tab.playwright.locator('#nav-6').click(); await state();
  let before = await read(); assert.equal(before.current,'nav-6'); assert.equal(before.focus,'q6');
  const point = await tab.playwright.evaluate(() => { const r=document.querySelector('.passage-scroll').getBoundingClientRect(); return [r.x+r.width/2,r.y+r.height/2]; });
  await tab.scroll(point,'down',1); await state();
  let after = await read(); assert.equal(after.current,'nav-6'); assert.equal(after.questions,before.questions); assert.ok(after.passage>before.passage);
  evidence.push({test:'left scroll preserves current question and right scroll',passed:true,before,after});

  await tab.playwright.locator('[name=q6][value=TRUE]').check();
  await tab.playwright.locator('[data-flag="6"]').click();
  await tab.playwright.locator('#save-now').click(); await state(); before=await read();
  await tab.reload(); await state(); after=await read();
  assert.equal(after.current,'nav-6'); assert.equal(after.q6,'TRUE'); assert.equal(after.flag6,'true'); assert.ok(Math.abs(before.passage-after.passage)<2);
  evidence.push({test:'refresh preserves current question, answer, review flag and passage position',passed:true,before,after});

  await tab.playwright.locator('#nav-6').click();
  await tab.playwright.locator('[name=q7][value=TRUE]').check(); await state();
  after=await read(); assert.equal(after.current,'nav-7'); assert.equal(after.focus,'q7');
  const qpoint = await tab.playwright.evaluate(() => { const r=document.querySelector('.questions-scroll').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]; });
  await tab.scroll(qpoint,'down',0.03); await state();
  const visible = await tab.playwright.evaluate(() => {const a=document.activeElement.closest('.question').getBoundingClientRect(),b=document.querySelector('.questions-scroll').getBoundingClientRect();return a.bottom>b.top&&a.top<b.bottom;});
  if (visible) assert.equal((await read()).current,'nav-7');
  await tab.scroll(qpoint,'down',1); await state(); after=await read(); assert.notEqual(after.current,'nav-7');
  evidence.push({test:'right scroll respects visible answer focus, then follows newly visible questions',passed:true,after});

  for (const width of [1440,1280,1024,768,390]) {
    await viewport.set({width,height:844});
    await tab.playwright.locator('[data-jump="5"]').click(); await state();
    await tab.playwright.locator('[data-jump="1"]').click(); await state();
    const rect = await tab.playwright.evaluate(() => {
      const g=document.querySelector('[data-group=matching_headings]'), panel=document.querySelector('.questions-scroll').getBoundingClientRect();
      const h=g.querySelector('h3').getBoundingClientRect(), p=g.querySelector('.instructions').getBoundingClientRect();
      return {panelTop:panel.top,panelBottom:panel.bottom,titleTop:h.top,instructionBottom:p.bottom,focus:document.activeElement.tagName,current:document.querySelector('[aria-current=step]')?.id};
    });
    assert.ok(rect.titleTop>=rect.panelTop); assert.ok(rect.instructionBottom<=rect.panelBottom); assert.equal(rect.focus,'H3'); assert.equal(rect.current,'nav-1');
    evidence.push({test:'type switching reveals full title/instructions',width,passed:true,rect});
  }
  await tab.playwright.locator('#nav-9').click(); await state();
  assert.equal((await read()).current,'nav-9');
  await tab.playwright.locator('[name=q9]').fill('flexible mind');
  await tab.playwright.locator('#save-now').click(); await state();
  const mobileBefore=await read();
  await tab.playwright.locator('[data-show-pane=reading]').click(); await state();
  await tab.playwright.locator('[data-show-pane=questions]').click(); await state();
  const mobileAfter=await read(); assert.equal(mobileAfter.current,'nav-9'); assert.ok(Math.abs(mobileBefore.questions-mobileAfter.questions)<2);
  evidence.push({test:'mobile pane switch preserves current question and answer position',passed:true,before:mobileBefore,after:mobileAfter});
  await tab.playwright.locator('#nav-10').press('Enter'); await state(); assert.equal((await read()).current,'nav-10');
  await tab.playwright.locator('#save-now').click(); await state();
  evidence.push({test:'keyboard Enter question navigation',passed:true});
  return evidence;
}

export async function resultRegression(tab, viewport) {
  const evidence=[];
  const state=async()=>tab.getAXState({emit:false});
  const read=()=>tab.playwright.evaluate(()=>({
    current:document.querySelector('#numbers [aria-current=step]')?.dataset.result,
    currentCount:document.querySelectorAll('#numbers [aria-current=step]').length,
    main:[...document.querySelectorAll('.result-question')].filter(e=>!e.hidden).map(e=>{const r=e.getBoundingClientRect(),b=document.querySelector('.questions-scroll').getBoundingClientRect();return {n:e.id.slice(7),visible:Math.max(0,Math.min(r.bottom,b.bottom)-Math.max(r.top,b.top))};}).sort((a,b)=>b.visible-a.visible)[0]?.n
  }));
  for (const width of [1280,390]) {
    await viewport.set({width,height:844});
    await tab.playwright.locator('[data-result="4"]').click(); await state();
    assert.equal((await read()).current,'4');
    const point=await tab.playwright.evaluate(()=>{const r=document.querySelector('.questions-scroll').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];});
    await tab.scroll(point,'down',1); await state();
    let now=await read(); assert.notEqual(now.current,'4'); assert.equal(now.current,now.main); assert.equal(now.currentCount,1);
    await tab.playwright.locator('[data-result="7"]').press('Enter'); await state();
    now=await read(); assert.equal(now.current,'7'); assert.equal(now.currentCount,1);
    evidence.push({test:'result wheel sync and keyboard navigation with aria-current',width,passed:true,now});
  }
  return evidence;
}
