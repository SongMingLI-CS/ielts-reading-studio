(()=>{
  const form=document.getElementById('practice-form'); if(!form)return;
  const unitId=form.dataset.unitId, storageKey=`ielts-reading:${unitId}`;
  const fields=[...form.querySelectorAll('input[name^="q"],select[name^="q"]')];
  const numbers=[...new Set(fields.map(field=>field.name.replace(/^q/,'')))];
  const timer=document.getElementById('timer'), saveStatus=document.getElementById('autosave-status');
  const answeredCount=document.getElementById('answered-count'), totalCount=document.getElementById('total-count');
  const progressCopy=document.getElementById('progress-copy'), progressBar=document.getElementById('progress-bar');
  const mobileAnsweredCount=document.getElementById('mobile-answered-count');
  const practiceShell=document.querySelector('.practice-shell'), paneTabs=[...document.querySelectorAll('[data-show-pane]')];
  practiceShell.classList.add('mobile-tabs-enabled');
  let state={attempt_id:crypto.randomUUID(),answers:{},elapsed_seconds:0};
  try{state={...state,...JSON.parse(localStorage.getItem(storageKey)||'{}')}}catch(_error){}
  const started=Date.now()-((state.elapsed_seconds||0)*1000); let saveTimer;

  const collect=()=>{const answers={};for(const number of numbers){const set=fields.filter(field=>field.name===`q${number}`);const selected=set.find(field=>field.type==='radio'&&field.checked);const value=selected?.value??set.find(field=>field.type!=='radio')?.value??'';if(value!==''&&value!=null)answers[number]=value}return answers};
  const hydrate=()=>{for(const [number,value] of Object.entries(state.answers||{})){for(const field of fields.filter(item=>item.name===`q${number}`)){if(field.type==='radio')field.checked=field.value===value;else field.value=value}}};
  const updateProgress=()=>{const answers=collect(),count=Object.keys(answers).length,total=numbers.length,percent=total?count/total*100:0;answeredCount.textContent=count;totalCount.textContent=total;mobileAnsweredCount.textContent=count;progressCopy.textContent=`已完成 ${count} / ${total} 题`;progressBar.style.width=`${percent}%`;for(const block of form.querySelectorAll('.question'))block.classList.toggle('answered',Boolean(answers[block.dataset.number]))};
  const elapsed=()=>Math.max(0,Math.floor((Date.now()-started)/1000));
  const persistBrowser=()=>{state.answers=collect();state.elapsed_seconds=elapsed();localStorage.setItem(storageKey,JSON.stringify(state))};
  const save=async()=>{persistBrowser();saveStatus.classList.add('saving');saveStatus.innerHTML='<i></i> 正在保存';try{const response=await fetch(`/practice/${unitId}/save`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)});if(!response.ok)throw new Error('save failed');const data=await response.json();state.attempt_id=data.attempt_id;persistBrowser();saveStatus.classList.remove('saving','error');saveStatus.innerHTML='<i></i> 已保存到本机'}catch(_error){saveStatus.classList.remove('saving');saveStatus.classList.add('error');saveStatus.innerHTML='<i></i> 浏览器草稿已保存'}};
  const scheduleSave=()=>{persistBrowser();updateProgress();clearTimeout(saveTimer);saveTimer=setTimeout(save,650)};
  hydrate();updateProgress();
  for(const tab of paneTabs)tab.addEventListener('click',()=>{const pane=tab.dataset.showPane;practiceShell.dataset.mobilePane=pane;for(const item of paneTabs)item.setAttribute('aria-selected',String(item===tab));practiceShell.scrollIntoView({behavior:'smooth',block:'start'})});
  setInterval(()=>{const seconds=elapsed();timer.textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;if(seconds%15===0)persistBrowser()},1000);
  form.addEventListener('input',scheduleSave);form.addEventListener('change',scheduleSave);window.addEventListener('beforeunload',persistBrowser);
  form.addEventListener('submit',async event=>{event.preventDefault();state.answers=collect();state.elapsed_seconds=elapsed();const button=form.querySelector('button[type="submit"]');button.disabled=true;button.textContent='正在判分…';try{const response=await fetch(`/practice/${unitId}/submit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)});if(!response.ok)throw new Error('submit failed');const data=await response.json();localStorage.removeItem(storageKey);const panel=document.getElementById('practice-result');panel.hidden=false;panel.innerHTML=`<div class="score-orb"><strong>${data.correct}</strong><span>/ ${data.total}</span></div><div><p class="eyebrow">Completed</p><h2>本次练习已保存在本机</h2><p>用时 ${Math.floor((data.elapsed_seconds||0)/60)} 分 ${String((data.elapsed_seconds||0)%60).padStart(2,'0')} 秒</p><a class="button" href="/practice/${unitId}/analysis">查看答案与逐题解析 →</a></div>`;panel.scrollIntoView({behavior:'smooth'});button.textContent='已提交'}catch(_error){button.disabled=false;button.textContent='提交失败，请重试';saveStatus.classList.add('error')}});
})();
