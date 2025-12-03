async function createMatchFromForm(formId = 'form-add-match', msgId = 'admin-msg'){
  const form = document.getElementById(formId);
  const fd = new FormData(form);
  const res = await fetch('/api/matches', {method:'POST', body: fd});
  const data = await res.json();
  const msg = document.getElementById(msgId);
  if(res.ok && data.ok){
    msg.innerText = 'Match créé (id:' + data.id + ') — recharge la page pour voir.';
    form.reset();
  } else {
    msg.innerText = 'Erreur: ' + (data.error || 'inconnu');
  }
}
