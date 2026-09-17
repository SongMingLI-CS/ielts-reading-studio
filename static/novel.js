(()=>{
  const active=document.querySelector('.run-state.queued,.run-state.running');
  if(!active)return;
  window.setTimeout(()=>window.location.reload(),5000);
})();
