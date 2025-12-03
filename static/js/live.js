// simple polling for live matches; update any UI elements that need live info
async function pollLive(){
  try{
    const res = await fetch('/api/matches?status=live');
    if(!res.ok) return;
    const matches = await res.json();
    // custom: find elements with data-live-id and update score
    document.querySelectorAll('[data-live-id]').forEach(el=>{
      const id = Number(el.getAttribute('data-live-id'));
      const m = matches.find(x=>x.id===id);
      if(m){
        el.innerText = m.score_a + ' - ' + m.score_b;
      }
    });
  }catch(e){
    // ignore
  }
}
setInterval(pollLive, 8000);
document.addEventListener('DOMContentLoaded', pollLive);
