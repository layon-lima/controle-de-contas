/* Interface portátil: nenhuma dependência de framework, sem banco no navegador. */
'use strict';
const $=id=>document.getElementById(id);
const token=window.__CONTAS_TOKEN__;
let accounts=[], availableTags=[], selected=null, activeTab='open', dialogSave=null;
const checked=new Set();
let activePreviewTicket=null;
const money=n=>'R$ '+Number(n||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
const localToday=()=>{
  const d=new Date();
  return String(d.getFullYear())+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
};
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
  const valid=new Set(accounts.map(a=>a.id));
  for(const id of checked)if(!valid.has(id))checked.delete(id);
  const choose=$('tagFilter'),previous=choose.value;
  choose.replaceChildren(new Option('Todas as TAGs',''));
  for(const tag of availableTags)choose.add(new Option(tag.name,String(tag.id)));
  if([...choose.options].some(o=>o.value===previous))choose.value=previous;
  render();
}
function render(){
  const totals={'Vencida':[0,0],'Próxima':[0,0],'Em dia':[0,0],'Paga':[0,0]};
  for(const a of accounts){
    if(a.status==='Paga'){
      // O card Pagas representa TODO o histórico, não apenas o mês atual.
      totals.Paga[0]++;
      totals.Paga[1]+=Number(a.valor_pago??a.valor??0);
    }else if(totals[a.status]){totals[a.status][0]++;totals[a.status][1]+=Number(a.valor||0);}
  }
  for(const [status,ids] of Object.entries({'Vencida':['kOver','vOver'],'Próxima':['kSoon','vSoon'],'Em dia':['kOk','vOk'],'Paga':['kPaid','vPaid']})){
    $(ids[0]).textContent=totals[status][0];$(ids[1]).textContent=money(totals[status][1]);
  }
  $('paidCard').setAttribute('aria-pressed',String(activeTab==='paid'));
  $('backToOpen').hidden=activeTab!=='paid';
  $('listTitle').textContent=activeTab==='paid'?'Contas pagas':'Visão geral';
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
  updateBulkBar();
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
       '<td><div class="vendor"><input class="bulk-check" type="checkbox" aria-label="Selecionar conta '+esc(a.fornecedor)+'" '+(checked.has(a.id)?'checked':'')+'><span class="vendor-avatar" style="background:'+avatarColor(a.fornecedor)+'">'+esc(initials(a.fornecedor))+'</span><span class="vendor-name">'+esc(a.fornecedor)+'</span></div></td>'
      +'<td data-label="Observações" class="obs" title="'+esc(a.observacoes||'')+'">'+esc(a.observacoes||'—')+'</td>'
      +'<td data-label="TAG">'+tagHtml+'</td>'
      +'<td data-label="Vencimento"><div class="due">'+esc(dateBR(a.vencimento))+'<small class="'+(dueDescription.includes('atrás')?'late':'')+'">'+esc(a.status==='Paga'?'Pago':dueDescription)+'</small></div></td>'
      +'<td data-label="Valor" class="value">'+esc(money(a.valor))+'</td>'
      +'<td data-label="Status"><span class="pill '+esc(a.status.replace(/\s/g,''))+'">'+esc(a.status)+'</span></td>'
      +'<td data-label="Sugerido em" class="date-time">'+esc(timeBR(a.suggested_at))+'</td>'
      +'<td data-label="Enviado em" class="date-time">'+esc(timeBR(a.enviado_pagamento_em))+'</td>';
    tr.addEventListener('click',()=>select(a.id));
    const checkbox=tr.querySelector('.bulk-check');
    checkbox.addEventListener('click',e=>e.stopPropagation());
    checkbox.addEventListener('dblclick',e=>e.stopPropagation());
    checkbox.addEventListener('change',()=>{
      if(checkbox.checked)checked.add(a.id);else checked.delete(a.id);
      updateBulkBar();
    });
    tr.addEventListener('dblclick',e=>openMenu(a.id,e));
    tr.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();openMenu(a.id,e);}});
    body.appendChild(tr);
  }
}
function updateBulkBar(){
  const count=checked.size;
  $('bulkBar').hidden=count===0;
  $('bulkCount').textContent=count+' conta'+(count===1?'':'s')+' selecionada'+(count===1?'':'s');
}
function checkedIds(){
  return [...checked].filter(id=>accounts.some(a=>a.id===id)).sort((a,b)=>a-b);
}
function clearChecked(){checked.clear();render();}
async function suggestBulk(){
  const ids=checkedIds();if(!ids.length)return;
  if(!confirm('Sugerir '+ids.length+' conta(s) no mobile?'))return;
  try{
    const result=await api('/api/batch/suggest','POST',{ids});
    notice(result.count+' contas sugeridas para pagamento.');
    clearChecked();await reload();
  }catch(e){notice(e.message||String(e),true)}
}
async function sendBulk(){
  const ids=checkedIds();if(!ids.length)return;
  try{
    const prepared=await api('/api/batch/prepare-send','POST',{ids});
    await copyText(prepared.text); // gravação somente APÓS cópia confirmada
    const result=await api('/api/batch/confirm-send','POST',{nonce:prepared.nonce});
    notice(result.count+' contas copiadas e registradas como enviadas.');
    clearChecked();await reload();
  }catch(e){notice(e.message||String(e),true)}
}
function tagsBulk(){
  const ids=checkedIds();if(!ids.length)return;
  const options=availableTags.map(t=>
    '<label class="tag-option"><input type="checkbox" value="'+t.id+'"> '+esc(t.name)+'</label>'
  ).join('');
  showModal('Adicionar TAGs em lote',
    'As TAGs selecionadas serão adicionadas às '+ids.length+' contas, sem apagar as que já possuem.',
    '<div id="tagBulkOptions" class="tag-options">'+
      (options||'<span class="empty">Nenhuma TAG cadastrada</span>')+
    '</div><label class="field" style="margin-top:15px">Criar nova TAG (opcional)'+
    '<input id="tagBulkNew" maxlength="60" placeholder="Nome da TAG"></label>',
    'Adicionar TAGs',async()=>{
      const tag_ids=[...$('tagBulkOptions').querySelectorAll('input:checked')]
        .map(x=>Number(x.value));
      const result=await api('/api/batch/tags','POST',{
        ids,tag_ids,new_tag:$('tagBulkNew').value
      });
      notice('TAGs adicionadas a '+result.count+' contas.');
      clearChecked();await reload();return true;
    });
}
function payBulk(){
  const ids=checkedIds();if(!ids.length)return;
  const today=localToday();
  showModal('Pagamento em lote',
    'Confirme o pagamento das '+ids.length+' contas selecionadas. Operação manual sem comprovante.',
    '<label class="field">Data do pagamento<input id="bulkPaidDate" type="date" value="'+today+'"></label>'+
    '<p class="hint">Contas já pagas serão ignoradas. Comprovantes são anexados individualmente pelo menu de cada conta.</p>',
    'Registrar '+ids.length+' pagamentos',async()=>{
      const result=await api('/api/batch/pay','POST',{
        ids,data_pagamento:$('bulkPaidDate').value
      });
      const failures=Object.keys(result.errors||{}).length;
      notice(result.count+' pagamentos registrados.'+(failures?' '+failures+' pendentes/recusados.':''),
             failures>0);
      clearChecked();await reload();return true;
    });
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
  menu.querySelector('[data-action="reanalyze"]').disabled=!a.has_pdf;
  menu.querySelector('[data-action="cancel-suggestion"]').disabled=!a.suggested_at;
  menu.querySelector('[data-action="copy-code"]').disabled=!String(a.codigo_pagamento||'').trim();
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
function closeModal(){
  $('backdrop').classList.remove('show');dialogSave=null;
  const ticket=activePreviewTicket;activePreviewTicket=null;
  if(ticket)api('/api/import/cancel','POST',{ticket}).catch(()=>{});
}
const accountFields=[
  ['fornecedor','Fornecedor *','text'],['valor','Valor (R$) *','number'],
  ['vencimento','Vencimento *','date'],['documento','CPF / CNPJ','text'],
  ['pagador','Pagador','text'],['referencia','Referência','text'],
  ['codigo_pagamento','PIX / Linha digitável','text'],
  ['tipo_pagamento','Tipo de pagamento','text'],
  ['observacoes','Observações','textarea']
];
function accountModal(a,preview=null,rerun=null){
  if(preview?.ticket)activePreviewTicket=preview.ticket;
  const analysis=preview||rerun;
  const suggested=analysis?.fields||{};
  const originalFilename=preview&&typeof preview.original_filename==='string'
    ? preview.original_filename : '';
  const unsafe=(analysis?.insecure_fields||[]).map(v=>String(v));
  const form=accountFields.map(([key,title,type])=>{
    const wide=['fornecedor','codigo_pagamento','observacoes'].includes(key);
    const predictedValue=suggested[key];
    const readable=predictedValue!==null&&predictedValue!==undefined&&String(predictedValue).trim()!=='';
    const fallbackValue=a?(a[key]??''):(key==='vencimento'?localToday():'');
    // Cadastro inicial: Observacoes inicia pelo nome do PDF original,
    // independentemente das sugestoes OCR. Em releitura de conta ja existente,
    // preservar as Observacoes cadastradas (sem mudar o nome do documento).
    const value=key==='observacoes'&&preview
      ? (originalFilename|| (readable?predictedValue:fallbackValue))
      : key==='observacoes'&&a ? fallbackValue
      : analysis&&readable?predictedValue:fallbackValue;
    const controls=type==='textarea'
      ?'<textarea name="'+key+'">'+esc(value)+'</textarea>'
      :'<input name="'+key+'" type="'+type+'" '+(type==='number'?'step="0.01" min="0.01"':'')+' value="'+esc(value)+'" '+(['fornecedor','valor','vencimento'].includes(key)?'required':'')+'>';
    return '<label class="field '+(wide?'wide':'')+'">'+title+controls+'</label>';
  }).join('');
  const fieldNames={recebedor:'Fornecedor',fornecedor:'Fornecedor',pagador:'Pagador',referencia:'Referência',valor:'Valor',vencimento:'Vencimento',codigo_pagamento:'Código de pagamento'};
  const warning=unsafe.length
    ? '<div class="ocr-note warning"><b>Conferir antes de salvar:</b> o leitor não confirmou com segurança '+esc([...new Set(unsafe.map(x=>fieldNames[x]||x))].join(', '))+'. Corrija os campos diretamente abaixo.</div>'
    : '<div class="ocr-note"><b>PDF analisado pelo OCR aprimorado.</b> Confira os dados preenchidos e confirme o cadastro.</div>';
  const extraWarnings=(analysis?.warnings||[]).filter(x=>typeof x==='string'&&x.trim());
  const comparison=analysis?.ocr_comparison||{};
  const additional=extraWarnings.length
    ? '<div class="ocr-note warning"><b>Conferência da leitura:</b> '+
       extraWarnings.map(item=>esc(item)).join(' ')+'</div>' : '';
  const modeNote=comparison.mode==='conservador'
    ? '<p class="hint">OCR aprimorado ativo: o leitor original tem prioridade. A análise complementar só preenche campos vazios e sinaliza divergências.</p>'
    : '';
  const intro=preview?'Leitura concluída. Nada será cadastrado antes da sua confirmação.':
    rerun?'Leitura do PDF existente refeita. Confira e corrija antes de alterar o cadastro.':
    'Os dados são salvos no banco local existente. O PDF é opcional.';
  showModal(preview?'Conferir leitura do PDF':rerun?'Revisar cadastro pelo PDF':a?'Editar conta':'Nova conta',intro,
    (analysis?warning+additional+modeNote:'')+'<form id="accountForm" class="formgrid">'+form+'</form>',
    preview?'Confirmar e salvar PDF':a?'Salvar alterações':'Criar conta',async()=>{
      const form=$('accountForm');
      if(!form.reportValidity())return false;
      const values=Object.fromEntries(new FormData(form));
      const route=preview?'/api/import/commit':a?'/api/accounts/'+a.id+'/edit':'/api/accounts';
      const saved=await api(route,'POST',preview?{...values,ticket:preview.ticket}:values);
      if(preview){
        notice(saved.organized?'Conta criada e PDF organizado na pasta de boletos.':'Conta criada. Configure a pasta-base para organizar os boletos.');
        setAccountView('open');
      }else notice(a?'Conta atualizada.':'Conta cadastrada.');
      await reload();return true;
    });
}
function payModal(a){
  if(a.status==='Paga'){notice('Conta já paga.',true);return}
  const now=localToday();
  showModal('Registrar pagamento',a.fornecedor+' · '+money(a.valor),
    '<label class="field">Data do pagamento<input type="date" id="paidDate" value="'+now+'"></label>'
    +'<label class="field" style="margin-top:15px">Comprovante PDF (opcional)<input type="file" id="receiptFile" accept=".pdf,application/pdf"></label>'
    +'<p class="hint">Com comprovante: o PDF será mesclado ao boleto, conferido pelo número de páginas e salvo na pasta da conta. Sem comprovante: registro manual.</p>',
    'Confirmar pagamento',async()=>{
      const paidOn=$('paidDate').value;
      if(!paidOn)throw new Error('Escolha a data.');
      const proof=$('receiptFile').files[0];
      if(proof){
        if(proof.size>30*1024*1024)throw new Error('Comprovante acima de 30 MB.');
        const response=await fetch('/api/accounts/'+a.id+'/pay-with-receipt',{
          method:'POST',headers:{'X-Local-Token':token,
            'Content-Type':'application/pdf','X-Paid-Date':paidOn},body:proof
        });
        const result=await response.json().catch(()=>({error:'Resposta de pagamento inválida.'}));
        if(!response.ok||!result.ok)throw new Error(result.error||'Pagamento não registrado.');
        notice('Pagamento registrado. Comprovante mesclado ao PDF ('+result.pages+' páginas).');
      }else{
        await api('/api/accounts/'+a.id+'/pay','POST',{data_pagamento:paidOn});
        notice('Pagamento manual registrado sem comprovante.');
      }
      setAccountView('paid');await reload();return true;
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
    else if(kind==='attach')$('uploadFile').click();
    else if(kind==='reanalyze'){
      const read=await api('/api/accounts/'+a.id+'/analyze');
      accountModal(a,null,read);
    }
    else if(kind==='copy-code'){
      const code=String(a.codigo_pagamento||'').trim();
      if(!code)throw new Error('Esta conta não tem PIX ou código digitável.');
      await copyText(code);
      notice('Código PIX / linha digitável copiado.');
    }
    else if(kind==='receipt')await showPDF(true);
    else if(kind==='send')await sendPayment();
    else if(kind==='cancel-suggestion'){
      if(!window.confirm('Cancelar a sugestão desta conta no mobile?'))return;
      const result=await api('/api/accounts/'+a.id+'/cancel-suggestion','POST',{});
      if(result.cancelled){notice('Sugestão cancelada.');await reload();}
    }
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
function setAccountView(mode){
  // A única navegação entre aberto e pago fica no card superior.
  // O histórico nunca é limitado ao mês e não mistura filtros incompatíveis.
  activeTab=mode==='paid'?'paid':'open';
  $('status').value=activeTab==='paid'?'Paga':'all';
  closeMenu();
  render();
}
function togglePaidView(){
  setAccountView(activeTab==='paid'?'open':'paid');
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
  notice(data.organized
    ? 'PDF salvo em BOLETOS por ano e mês: '+data.folder
    : 'PDF anexado na pasta provisória. Configure a pasta-base em Pasta boletos.');await reload();
}
$('bulkSuggest').addEventListener('click',suggestBulk);
$('bulkTags').addEventListener('click',tagsBulk);
$('bulkSend').addEventListener('click',sendBulk);
$('bulkPay').addEventListener('click',payBulk);
$('bulkClear').addEventListener('click',clearChecked);
$('status').addEventListener('change',()=>{
  const value=$('status').value;
  activeTab=value==='Paga'?'paid':'open';
  render();
});
$('paidCard').addEventListener('click',togglePaidView);
$('paidCard').addEventListener('keydown',e=>{
  if(e.key==='Enter'||e.key===' '){e.preventDefault();togglePaidView();}
});
$('backToOpen').addEventListener('click',()=>setAccountView('open'));
$('tagFilter').addEventListener('change',render);
$('search').addEventListener('input',render);
$('newAccount').addEventListener('click',()=>accountModal(null));
$('pdfImport').addEventListener('click',()=>$('importFile').click());
let importBusy=false;
async function importNewPDF(file){
  if(!file)return;
  if($('backdrop').classList.contains('show')){notice('Conclua ou cancele a janela atual antes de importar outro PDF.',true);return;}
  if(importBusy){notice('Uma leitura de PDF já está em andamento.',true);return;}
  if(!/\.pdf$/i.test(file.name||'')){notice('Selecione um documento PDF.',true);return;}
  if(file.size>30*1024*1024){notice('PDF maior que 30 MB.',true);return;}
  const button=$('pdfImport'),previous=button.innerHTML;
  importBusy=true;button.disabled=true;button.textContent='Lendo PDF e preenchendo…';
  try{
    const response=await fetch('/api/import/preview',{
      method:'POST',
      headers:{'X-Local-Token':token,'Content-Type':'application/pdf',
               'X-Pdf-Name':encodeURIComponent(file.name),'X-Ocr-Mode':'hybrid'},
      body:file,
    });
    const result=await response.json().catch(()=>({error:'Resposta inválida do leitor OCR.'}));
    if(!response.ok||!result.ok)throw new Error(result.error||'Não foi possível ler o PDF.');
    accountModal(null,result);
  }catch(err){notice(err.message||String(err),true);}
  finally{importBusy=false;button.disabled=false;button.innerHTML=previous;}
}
$('importFile').addEventListener('change',e=>{
  const file=e.target.files[0];e.target.value='';
  if(file)importNewPDF(file);
});
// Soltar PDF sempre cria nova conta; não altera o PDF da linha selecionada.
let dragDepth=0;
function fileDrag(e){return Array.from(e.dataTransfer?.types||[]).includes('Files');}
function hideDropZone(){dragDepth=0;$('dropZone').classList.remove('visible');}
document.addEventListener('dragenter',e=>{
  if(!fileDrag(e))return;
  e.preventDefault();dragDepth++;$('dropZone').classList.add('visible');
});
document.addEventListener('dragover',e=>{
  if(!fileDrag(e))return;
  e.preventDefault();e.dataTransfer.dropEffect='copy';$('dropZone').classList.add('visible');
});
document.addEventListener('dragleave',e=>{
  if(!fileDrag(e))return;
  e.preventDefault();dragDepth=Math.max(0,dragDepth-1);
  if(!dragDepth)$('dropZone').classList.remove('visible');
});
document.addEventListener('drop',e=>{
  if(!fileDrag(e))return;
  e.preventDefault();const files=Array.from(e.dataTransfer?.files||[]);
  hideDropZone();
  if(files.length!==1){notice('Arraste apenas um PDF de cada vez.',true);return;}
  importNewPDF(files[0]);
});
window.addEventListener('blur',hideDropZone);
$('uploadFile').addEventListener('change',async e=>{
  const file=e.target.files[0];if(file){try{await uploadAccountPDF(file)}catch(err){notice(err.message,true)}}
  e.target.value='';
});
$('folderBtn').addEventListener('click',async()=>{
  try{
    const data=await api('/api/settings/folder');
    showModal('Pasta de boletos',
      'Pasta-base usada pelo Controle de Contas. Os PDFs são separados por ano e mês de vencimento.',
      '<label class="field wide">Pasta-base no PC<input id="folderPath" type="text" placeholder="C:\\Meus Documentos\\Boletos" value="'+esc(data.folder||'')+'"></label>'+
      '<p class="hint">Escolha uma pasta que já exista. O aplicativo criará as subpastas BOLETOS ANO / MÊS automaticamente.</p>',
      'Salvar pasta',async()=>{
        const folder=$('folderPath').value.trim();
        const saved=await api('/api/settings/folder','POST',{folder});
        notice('Pasta-base atualizada: '+saved.folder);
        return true;
      });
  }catch(e){notice(e.message||String(e),true)}
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
