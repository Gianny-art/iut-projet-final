async function placeBet(){
  const form = document.getElementById('betForm');
  if(!form){ return; }
  const fd = new FormData(form);
  const resp = await fetch('/place_bet', {method:'POST', body: fd});
  const data = await resp.json();
  const el = document.getElementById('betResult');
  if(resp.ok){
    el.innerText = 'Mise placée ! Nouveau solde: ' + data.balance + ' F';
  } else {
    el.innerText = 'Erreur: ' + (data.error || 'unknown');
    if(resp.status===401){
      window.location.href = '/login';
    }
  }
}
