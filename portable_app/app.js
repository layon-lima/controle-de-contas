/* Interface portátil: nenhuma dependência de framework, sem banco no navegador. */
'use strict';
const $=id=>document.getElementById(id);
const token=window.__CONTAS_TOKEN__;
let accounts=[], availableTags=[], selected=null, activeTab='open', dialogSave=null;
const money=n=>'R$ '+Number(n||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
const esc=v=>String(v===null||v===undefined?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dayLabel=v=>{
  if(!v)return '';
  const today=new Date();today.setHours(0,0,0,0);
  const due=new Date(String(v).slice(0,10)+'T00:00:00');
  const distance=Math.round((due-today)/86400000);
  if(!Number.isFinite(distance))return '';
  return distance<0?Math.abs(distance)+' dias atrás':distance===0?'Hoje':distance===1?'Amanhã':'Em '+distance+' dias';
};
const dateBR=v=>{
  if(!v)return '—';
  const m=String(v).match(/^(\d{4})-(\d\d)-(\d\d)/);
  return m?m[3]+'/'+m[2]+'/'+m[1]:String(v);
};
const timeBR=v=>{
  if(!v)return '—';
  const str=String(v);
  if(str.endsWith('Z')||/[+-]\d\d:\d\d$/.test(str)){
    const d=new Date(str);
    return Number.isNaN(d.getTime())?str:d.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'});
  }
  return dateBR(str)+(str.length>10?' '+str.slice(11,16):'');
};
const initials=name=>(String(name||'').match(/\b[\p{L}\p{N}]/gu)||[]).slice(0,2).join('').toUpperCase()||'C';
const avatarColor=name=>['#377eef','#07885d','#9c66e9','#fa586a','#2c9eac'][[...String(name)].reduce((n,c)=>n+c.charCodeAt(0),0)%5];

async function api(route,method='GET',data){
  const opts={method,headers:{'X-Local-Token':token}};
  if(data!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(data);}
  const response=await fetch(route,opts);
  const body=await response.json().catch(()=>({error:'Resposta inválida do servidor.'}));
  if(!response.ok||body.ok===false)throw new Error(body.error||'Operação não concluída.');
  return body;
}
function notice(message,error=false){
  const toast=$('toast');toast.textContent=message;toast.className='toast '+(error?'error':'ok');toast.style.display='block';
  clearTimeout(notice.timer);notice.timer=setTimeout(()=>toast.style.display='none',4200);
}
async function reload(){
  const data=await api('/api/accounts');
  accounts=data.accounts||[];availableTags=data.tags||[];
  if(selected&&!accounts.some(a=>a.id===selected))selected=null;
  const choose=$('tagFilter'),previous=choose.value;
  choose.replaceChildren(new Option('Todas as TAGs',''));
  for(const tag of availableTags)choose.add(new Option(tag.name,String(tag.id)));
  if([...choose.options].some(o=>o.value===previous))choose.value=previous;
  render();
}
function render(){
  const totals={'Vencida':[0,0],'Próxima':[0,0],'Em dia':[0,0],'Paga':[0,0]};
  const today=new Date().toISOString().slice(0,7);
  for(const a of accounts){
    if(a.status==='Paga'){
      if(String(a.data_pagamento||'').slice(0,7)===today){totals.Paga[0]++;totals.Paga[1]+=Number(a.valor_pago||a.valor||0);}
    }else if(totals[a.status]){totals[a.status][0]++;totals[a.status][1]+=Number(a.valor||0);}
  }
  for(const [status,ids] of Object.entries({'Vencida':['kOver','vOver'],'Próxima':['kSoon','vSoon'],'Em dia':['kOk','vOk'],'Paga':['kPaid','vPaid']})){
    $(ids[0]).textContent=totals[status][0];$(ids[1]).textContent=money(totals[status][1]);
  }
  const requested=$('status').value,tagId=$('tagFilter').value;
  const query=$('search').value.trim().toLocaleLowerCase('pt-BR');
  const visible=accounts.filter(a=>{
    if(activeTab==='paid'&&a.status!=='Paga')return false;
    if(activeTab==='open'&&a.status==='Paga')return false;
    if(requested!=='all'&&a.status!==requested)return false;
    if(tagId&&!(a.tags||[]).some(t=>String(t.id)===tagId))return false;
    if(query&&![
      a.fornecedor,a.observacoes,a.documento,a.referencia,a.pagador,a.valor,
      a.vencimento,a.codigo_pagamento,...(a.tags||[]).map(t=>t.name)
    ].some(v=>String(v||'').toLocaleLowerCase('pt-BR').includes(query)))return false;
    return true;
  });
  $('count').textContent=visible.length+' registros';
  const body=$('rows');body.replaceChildren();
  if(!visible.length){
    const tr=document.createElement('tr'),td=document.createElement('td');
    td.colSpan=8;td.style.cssText='text-align:center;padding:48px;color:#8494a9';
    td.textContent=accounts.length?'Nenhuma conta encontrada com estes filtros.':'Nenhuma conta cadastrada.';
    tr.appendChild(td);body.appendChild(tr);return;
  }
  for(const a of visible){
    const tr=document.createElement('tr');
    tr.dataset.id=a.id;tr.tabIndex=0;
    if(selected===a.id)tr.classList.add('selected');
    tr.title='Dê dois cliques para abrir as ações da conta';
    const tagHtml=(a.tags||[]).map(t=>'<span class="tag">'+esc(t.name)+'</span>').join(' ')||'<span class="empty">—</span>';
    const dueDescription=dayLabel(a.vencimento);
    tr.innerHTML=
       '<td><div class="vendor"><span class="vendor-avatar" style="background:'+avatarColor(a.fornecedor)+'">'+esc(initials(a.fornecedor))+'</span><span class="vendor-name">'+esc(a.fornecedor)+'</span></div></td>'
      +'<td class="obs" title="'+esc(a.observacoes||'')+'">'+esc(a.observacoes||'—')+'</td>'
      +'<td>'+tagHtml+'</td>'
      +'<td><div class="due">'+esc(dateBR(a.vencimento))+'<small class="'+(dueDescription.includes('atrás')?'late':'')+'">'+esc(a.status==='Paga'?'Pago':dueDescription)+'</small></div></td>'
      +'<td class="value">'+esc(money(a.valor))+'</td>'
      +'<td><span class="pill '+esc(a.status.replace(/\s/g,''))+'">'+esc(a.status)+'</span></td>'
      +'<td class="date-time">'+esc(timeBR(a.suggested_at))+'</td>'
      +'<td class="date-time">'+esc(timeBR(a.enviado_pagamento_em))+'</td>';
    tr.addEventListener('click',()=>select(a.id));
    tr.addEventListener('dblclick',e=>openMenu(a.id,e));
    tr.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();openMenu(a.id,e);}});
    body.appendChild(tr);
  }
}
function select(id){
  selected=id;
  for(const tr of $('rows').children)tr.classList.toggle('selected',Number(tr.dataset.id)===id);
}
function current(){return accounts.find(a=>a.id===selected);}
function closeMenu(){$('menu').classList.remove('visible')}
function openMenu(id,event){
  selected=id;select(id);
  const a=current();if(!a)return;
  const menu=$('menu');
  menu.classList.add('visible');
  menu.querySelector('[data-action="pdf"]').disabled=!a.has_pdf;
  menu.querySelector('[data-action="receipt"]').disabled=!a.has_receipt;
  menu.querySelector('[data-action="pay"]').disabled=a.status==='Paga';
  menu.querySelector('[data-action="delete"]').disabled=a.status==='Paga';
  const rect=event.currentTarget.getBoundingClientRect();
  const right=Math.min(window.innerWidth-menu.offsetWidth-12,Math.max(12,rect.left+rect.width*.57));
  const top=Math.min(window.innerHeight-menu.offsetHeight-12,Math.max(12,rect.top+28));
  menu.style.left=Math.max(12,right)+'px';menu.style.top=Math.max(12,top)+'px';
}
async function showPDF(receipt=false){
  const a=current();if(!a)return;
  const response=await fetch('/api/accounts/'+a.id+'/pdf'+(receipt?'?receipt=1':''),{headers:{'X-Local-Token':token}});
  if(!response.ok){const e=await response.json().catch(()=>({}));throw new Error(e.error||'PDF não encontrado.');}
  const blob=await response.blob();
  const url=URL.createObjectURL(blob);
  const viewer=window.open(url,'_blank');
  if(!viewer)notice('Permita popups para visualizar o PDF.',true);
  setTimeout(()=>URL.revokeObjectURL(url),60000);
}
async function copyText(text){
  if(navigator.clipboard&&navigator.clipboard.writeText){
    await navigator.clipboard.writeText(text);
  }else{
    const field=document.createElement('textarea');field.value=text;field.style.cssText='position:fixed;opacity:0';
    document.body.appendChild(field);field.focus();field.select();
    const ok=document.execCommand('copy');field.remove();
    if(!ok)throw new Error('Não foi possível copiar. Autorize o acesso à área de transferência.');
  }
}
async function sendPayment(){
  const a=current();if(!a)return;
  const ready=await api('/api/accounts/'+a.id+'/prepare-send','POST',{});
  // NUNCA registrar data antes da confirmação de cópia bem-sucedida.
  await copyText(ready.text);
  await api('/api/accounts/'+a.id+'/confirm-send','POST',{nonce:ready.nonce});
  notice('Dados copiados e envio registrado com sucesso.');
  await reload();
}
function showModal(title,description,bodyHTML,label,onSave){
  $('dialogTitle').textContent=title;$('dialogIntro').textContent=description;
  $('dialogBody').innerHTML=bodyHTML;$('save').textContent=label;
  dialogSave=onSave;$('backdrop').classList.add('show');
  $('dialogBody').querySelector('input,select,textarea')?.focus();
}
function closeModal(){$('backdrop').classList.remove('show');dialogSave=null}
const accountFields=[
  ['fornecedor','Fornecedor *','text'],['valor','Valor (R$) *','number'],
  ['vencimento','Vencimento *','date'],['documento','CPF / CNPJ','text'],
  ['pagador','Pagador','text'],['referencia','Referência','text'],
  ['codigo_pagamento','PIX / Linha digitável','text'],
  ['tipo_pagamento','Tipo de pagamento','text'],
  ['observacoes','Observações','textarea']
];
function accountModal(a){
  const form=accountFields.map(([key,title,type])=>{
    const wide=['fornecedor','codigo_pagamento','observacoes'].includes(key);
    const value=a?a[key]||'':key==='vencimento'?new Date().toISOString().slice(0,10):'';
    const controls=type==='textarea'
      ?'<textarea name="'+key+'">'+esc(value)+'</textarea>'
      :'<input name="'+key+'" type="'+type+'" '+(type==='number'?'step="0.01" min="0.01"':'')+' value="'+esc(value)+'" '+(['fornecedor','valor','vencimento'].includes(key)?'required':'')+'>';
    return '<label class="field '+(wide?'wide':'')+'">'+title+controls+'</label>';
  }).join('');
  showModal(a?'Editar conta':'Nova conta','Os dados são salvos no banco local existente. O PDF é opcional.',
    '<form id="accountForm" class="formgrid">'+form+'</form>',
    a?'Salvar alterações':'Criar conta',async()=>{
      const form=$('accountForm');
      if(!form.reportValidity())return false;
      const values=Object.fromEntries(new FormData(form));
      await api(a?'/api/accounts/'+a.id+'/edit':'/api/accounts', 'POST',values);
      notice(a?'Conta atualizada.':'Conta cadastrada.');
      await reload();return true;
    });
}
function payModal(a){
  if(a.status==='Paga'){notice('Conta já paga.',true);return}
  const now=new Date().toISOString().slice(0,10);
  showModal('Registrar pagamento',a.fornecedor+' · '+money(a.valor),
    '<label class="field">Data do pagamento<input type="date" id="paidDate" value="'+now+'"></label>'
    +'<p class="hint">Registro sem comprovante. Você poderá consultar a conta normalmente; a versão clássica permanece disponível para conciliação avançada de PDFs.</p>',
    'Confirmar pagamento',async()=>{
      if(!$('paidDate').value)throw new Error('Escolha a data.');
      await api('/api/accounts/'+a.id+'/pay','POST',{data_pagamento:$('paidDate').value});
      notice('Pagamento registrado.');activeTab='paid';setTabs();await reload();return true;
    });
}
function tagModal(a){
  const ids=new Set((a.tags||[]).map(t=>Number(t.id)));
  const opts=availableTags.map(t=>
    '<label class="tag-option"><input type="checkbox" value="'+t.id+'" '+(ids.has(Number(t.id))?'checked':'')+'> '+esc(t.name)+'</label>'
  ).join('');
  showModal('Gerenciar TAGs',a.fornecedor,
    '<div id="tagOptions" class="tag-options">'+(opts||'<span class="empty">Nenhuma TAG criada</span>')+'</div>'
    +'<label class="field" style="margin-top:15px">Criar nova TAG (opcional)<input id="newTag" maxlength="60" placeholder="Nome da TAG"></label>',
    'Salvar TAGs',async()=>{
      const selectedTags=[...$('tagOptions').querySelectorAll('input:checked')].map(e=>Number(e.value));
      await api('/api/accounts/'+a.id+'/tags','POST',{ids:selectedTags,new_tag:$('newTag').value});
      notice('TAGs atualizadas.');await reload();return true;
    });
}
async function deleteAccount(a){
  if(!window.confirm('Excluir a conta de '+a.fornecedor+'?\nA exclusão é definitiva no cadastro. PDFs originais não serão apagados.'))return;
  await api('/api/accounts/'+a.id+'/delete','POST',{});
  selected=null;notice('Conta excluída.');await reload();
}
async function action(kind){
  const a=current();closeMenu();if(!a)return;
  try{
    if(kind==='pdf')await showPDF();
    else if(kind==='receipt')await showPDF(true);
    else if(kind==='send')await sendPayment();
    else if(kind==='pay')payModal(a);
    else if(kind==='edit')accountModal(a);
    else if(kind==='tags')tagModal(a);
    else if(kind==='delete')await deleteAccount(a);
    else if(kind==='suggest'){
      if(!window.confirm('Sugerir esta conta para pagamento no mobile?'))return;
      await api('/api/accounts/'+a.id+'/suggest','POST',{});
      notice('Sugestão enviada ao mobile.');await reload();
    }
  }catch(e){notice(e.message||String(e),true)}
}
function setTabs(){
  for(const button of document.querySelectorAll('.tab'))button.classList.toggle('active',button.dataset.tab===activeTab);
  $('status').value='all';
}
async function uploadAccountPDF(file){
  if(!selected)throw new Error('Selecione uma conta na lista antes de importar um PDF.');
  if(!file.name.toLowerCase().endsWith('.pdf'))throw new Error('Selecione um documento PDF.');
  if(file.size>30*1024*1024)throw new Error('PDF maior que 30 MB.');
  const response=await fetch('/api/accounts/'+selected+'/upload-pdf',{
    method:'POST',headers:{'X-Local-Token':token,'Content-Type':'application/pdf','X-Pdf-Name':encodeURIComponent(file.name)},
    body:file
  });
  const data=await response.json().catch(()=>({error:'Falha ao enviar PDF.'}));
  if(!response.ok||!data.ok)throw new Error(data.error||'Falha ao importar PDF.');
  notice('PDF anexado à conta selecionada.');await reload();
}
$('status').addEventListener('change',render);
$('tagFilter').addEventListener('change',render);
$('search').addEventListener('input',render);
for(const tab of document.querySelectorAll('.tab'))tab.addEventListener('click',()=>{
  activeTab=tab.dataset.tab;closeMenu();setTabs();render();
});
$('newAccount').addEventListener('click',()=>accountModal(null));
$('pdfImport').addEventListener('click',()=>{
  if(!selected){notice('Selecione uma conta primeiro. Depois clique em Importar PDF.',true);return}
  $('uploadFile').click();
});
$('uploadFile').addEventListener('change',async e=>{
  const file=e.target.files[0];if(file){try{await uploadAccountPDF(file)}catch(err){notice(err.message,true)}}
  e.target.value='';
});
$('mobileBtn').addEventListener('click',async()=>{
  try{const data=await api('/api/mobile-link');window.open(data.url,'_blank','noopener');}
  catch(e){notice(e.message,true)}
});
$('menuClose').addEventListener('click',closeMenu);
for(const b of document.querySelectorAll('.menu-item'))b.addEventListener('click',()=>action(b.dataset.action));
document.addEventListener('click',e=>{
  if(!e.target.closest('#menu')&&!e.target.closest('tbody tr'))closeMenu();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeMenu();closeModal();}});
$('cancel').addEventListener('click',closeModal);
$('backdrop').addEventListener('click',e=>{if(e.target===$('backdrop'))closeModal()});
$('save').addEventListener('click',async()=>{
  if(!dialogSave)return;
  $('save').disabled=true;
  try{const ok=await dialogSave();if(ok)closeModal()}
  catch(e){notice(e.message||String(e),true)}
  finally{$('save').disabled=false}
});
reload().catch(e=>notice('Não foi possível carregar o banco: '+e.message,true));
setInterval(()=>{
  if(!$('backdrop').classList.contains('show')&&!$('menu').classList.contains('visible'))
    reload().catch(()=>{});
},15000);
