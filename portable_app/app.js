/* Interface portátil: nenhuma dependência de framework, sem banco no navegador. */
'use strict';
const $=id=>document.getElementById(id);
let token=window.__CONTAS_TOKEN__;
let accounts=[], selected=null, activeTab='open', dialogSave=null;
let activePreviewTicket=null;


/* Pasta de boletos NO COMPUTADOR que abriu esta pagina. Em HTTPS/VPS o
   servidor nao pode enxergar caminhos C:\ do Windows. O navegador libera
   uma pasta real com showDirectoryPicker e guarda a permissao no IndexedDB. */
const WINDOWS_FOLDERS_DB='contas-windows-folders-v1';
const WINDOWS_FOLDERS_VERSION=2;
let pendingImportPDF=null;
// Manter o handle salvo no IndexedDB e em memoria para reutiliza-lo
// mesmo apos fechar e reabrir a interface.
let windowsFolderHandle=null,windowsFolderLoad=null;
function openWindowsFoldersDB(){
  return new Promise((resolve,reject)=>{
    const req=indexedDB.open(WINDOWS_FOLDERS_DB,WINDOWS_FOLDERS_VERSION);
    req.onupgradeneeded=()=>{
      const db=req.result;
      if(!db.objectStoreNames.contains('settings'))db.createObjectStore('settings');
      if(!db.objectStoreNames.contains('pending'))db.createObjectStore('pending',{keyPath:'id'});
    };
    req.onsuccess=()=>{const db=req.result;db.onversionchange=()=>db.close();resolve(db);};
    req.onblocked=()=>reject(new Error(
      'Outra aba do Controle de Contas esta usando a pasta. Feche as outras abas e atualize esta pagina.'
    ));
    req.onerror=()=>reject(req.error||new Error('Nao foi possivel guardar a pasta.'));
  });
}
async function readWindowsBoletoFolder(){
  if(windowsFolderHandle)return windowsFolderHandle;
  if(!windowsFolderLoad)windowsFolderLoad=(async()=>{
    const db=await openWindowsFoldersDB();
    try{
      const handle=await new Promise((resolve,reject)=>{
        const req=db.transaction('settings','readonly').objectStore('settings').get('boletos');
        req.onsuccess=()=>resolve(req.result||null);
        req.onerror=()=>reject(req.error||new Error('Nao foi possivel ler a pasta.'));
      });
      if(handle?.kind==='directory')windowsFolderHandle=handle;
      return windowsFolderHandle;
    }finally{db.close();}
  })().finally(()=>{windowsFolderLoad=null;});
  return windowsFolderLoad;
}
async function rememberWindowsBoletoFolder(handle){
  const db=await openWindowsFoldersDB();
  try{
    await new Promise((resolve,reject)=>{
      const tx=db.transaction('settings','readwrite');
      tx.objectStore('settings').put(handle,'boletos');
      tx.oncomplete=resolve;
      tx.onerror=()=>reject(tx.error||new Error('Falha ao salvar a pasta.'));
      tx.onabort=()=>reject(tx.error||new Error('Falha ao salvar a pasta.'));
    });
  }finally{db.close();}
  windowsFolderHandle=handle;
}
// Uma copia que falhou continua pendente mesmo apos fechar o navegador.
// Guardar no IndexedDB SOMENTE o PDF original selecionado pelo usuario,
// nunca chaves/tokens do servidor.
async function windowsCopyTransaction(mode,operation){
  const db=await openWindowsFoldersDB();
  try{
    return await new Promise((resolve,reject)=>{
      const tx=db.transaction('pending',mode);
      const store=tx.objectStore('pending');
      let result;
      try{result=operation(store);}catch(e){reject(e);return;}
      tx.oncomplete=()=>resolve(result?.result);
      tx.onerror=()=>reject(tx.error||new Error('Falha ao acessar fila local de PDFs.'));
      tx.onabort=()=>reject(tx.error||new Error('Falha ao salvar a copia pendente.'));
    });
  }finally{db.close();}
}
async function saveWindowsCopyPending(file,details,id){
  if(!file||!Number.isInteger(Number(id))||Number(id)<=0)
    throw new Error('Nao foi possivel identificar a conta para repetir a copia.');
  return windowsCopyTransaction('readwrite',store=>store.put({
    id:Number(id),file,details:{
      vencimento:String(details?.vencimento||''),
      observacoes:String(details?.observacoes||'')
    },addedAt:Date.now()
  }));
}
async function windowsPendingCopies(){
  const db=await openWindowsFoldersDB();
  try{return await new Promise((resolve,reject)=>{
    const req=db.transaction('pending','readonly').objectStore('pending').getAll();
    req.onsuccess=()=>resolve(req.result||[]);
    req.onerror=()=>reject(req.error||new Error('Nao foi possivel ler as copias pendentes.'));
  });}finally{db.close();}
}
async function removeWindowsCopyPending(id){
  return windowsCopyTransaction('readwrite',store=>store.delete(Number(id)));
}
function validateWindowsFolderRoot(root){
  if(!root)return;
  if(/^BOLETOS\s+\d{4}$/i.test(root.name)||/^\d{2}\s*-\s*/.test(root.name))
    throw new Error('Selecione a pasta BOLETOS principal, e nao a subpasta de ano ou mes. Use Trocar pasta.');
}
async function testWindowsFolderWrite(root){
  validateWindowsFolderRoot(root);
  const name='.contas-teste-acesso-'+Date.now().toString(36)+'.tmp';
  const file=await root.getFileHandle(name,{create:true});
  try{
    const writer=await file.createWritable();
    try{await writer.write('Teste de escrita do Controle de Contas.');await writer.close();}
    catch(e){await writer.abort().catch(()=>{});throw e;}
  }finally{
    await root.removeEntry(name).catch(()=>{});
  }
}
async function copyPendingWindowsPDFs(){
  const root=await prepareWindowsFolderCopy();
  if(!root)throw new Error('Selecione primeiro a pasta BOLETOS no Windows.');
  validateWindowsFolderRoot(root);
  const pending=await windowsPendingCopies();
  let copied=0;
  for(const entry of pending){
    const result=await copyPDFToWindowsFolder(entry.file,entry.details,entry.id);
    if(!result?.saved)throw new Error(
      copied+' PDF(s) copiado(s). Conta '+entry.id+': '+(result?.error||'Nao foi possivel gravar o PDF.')
    );
    await removeWindowsCopyPending(entry.id);
    copied++;
  }
  return copied;
}
async function copyOrQueueWindowsPDF(file,details,id){
  let result;
  try{result=await copyPDFToWindowsFolder(file,details,id);}
  catch(e){result={configured:true,saved:false,error:e.message||String(e)};}
  if(result?.saved||!result?.configured)return result;
  try{
    await saveWindowsCopyPending(file,details,id);
    return {...result,queued:true};
  }catch(e){
    return {...result,queued:false,
      error:(result.error||'Falha ao copiar para o Windows.')+
        ' Tambem nao foi possivel manter uma copia pendente no navegador: '+
        (e.message||String(e))};
  }
}
async function windowsFolderPermission(handle,request=false){
  if(!handle)return 'not-configured';
  if(typeof handle.queryPermission!=='function')return 'unsupported';
  let permission=await handle.queryPermission({mode:'readwrite'});
  // O navegador so permite pedir permissao em uma acao iniciada pelo usuario.
  if(permission!=='granted'&&request&&typeof handle.requestPermission==='function'){
    permission=await handle.requestPermission({mode:'readwrite'});
  }
  return permission;
}
async function prepareWindowsFolderCopy(){
  const handle=await readWindowsBoletoFolder();
  if(!handle)return null; // Mantem a opcao original de nao configurar a pasta.
  let permission;
  try{permission=await windowsFolderPermission(handle,true);}
  catch(_){throw new Error('A pasta '+handle.name+' esta lembrada. Abra Pasta boletos e autorize o acesso.');}
  if(permission!=='granted')throw new Error(
    'A pasta '+handle.name+' esta lembrada. Use Pasta boletos > Autorizar acesso. Nao precisa selecionar de novo.');
  return handle;
}
// Antecipar a leitura do IndexedDB, sem solicitar permissoes no carregamento.
readWindowsBoletoFolder().catch(()=>{});
const WINDOWS_MONTHS=['JANEIRO','FEVEREIRO','MARÇO','ABRIL','MAIO','JUNHO',
  'JULHO','AGOSTO','SETEMBRO','OUTUBRO','NOVEMBRO','DEZEMBRO'];
function safeWindowsPdfName(name){
  let text=String(name||'BOLETO').replace(/[<>:"/\\|?*\x00-\x1f]/g,' ').replace(/\s+/g,' ').trim();
  if(/\.pdf$/i.test(text))text=text.slice(0,-4);
  text=text.slice(0,110).replace(/[. ]+$/g,'')||'BOLETO';
  if(/^(CON|PRN|AUX|NUL|COM\d|LPT\d)$/i.test(text))text='BOLETO '+text;
  return text;
}
// A copia local nao depende da VPS; nao altera nem substitui o original nela.
async function copyPDFToWindowsFolder(file,accountInfo,accountId){
  const root=await readWindowsBoletoFolder();
  if(!root)return {configured:false,saved:false};
  validateWindowsFolderRoot(root);
  const permission=await windowsFolderPermission(root);
  if(permission!=='granted')return {configured:true,saved:false,
    error:'A pasta '+root.name+' ja esta lembrada. Clique em Pasta boletos > Autorizar acesso; nao precisa selecionar novamente.'};
  const due=String(accountInfo?.vencimento||'').slice(0,10);
  const match=/^(\d{4})-(\d{2})-\d{2}$/.exec(due);
  if(!match||Number(match[2])<1||Number(match[2])>12)
    return {configured:true,saved:false,error:'Nao foi possivel organizar a copia local sem vencimento valido.'};
  const year=match[1],month=Number(match[2]);
  const yearFolder=await root.getDirectoryHandle('BOLETOS '+year,{create:true});
  const monthFolder=await yearFolder.getDirectoryHandle(
    String(month).padStart(2,'0')+' - '+WINDOWS_MONTHS[month-1],{create:true});
  const stem=safeWindowsPdfName(accountInfo?.observacoes||file.name);
  // Um ID fixo evita sobrescrever uma conta diferente com o mesmo nome.
  const base=accountId?String(accountId)+' - '+stem:stem;
  let name=base+'.pdf';
  for(let i=1;i<1000;i++){
    try{await monthFolder.getFileHandle(name);name=base+' ('+i+').pdf';}
    catch(e){if(e.name==='NotFoundError')break;throw e;}
  }
  const target=await monthFolder.getFileHandle(name,{create:true});
  const writer=await target.createWritable();
  try{await writer.write(file);await writer.close();}
  catch(e){await writer.abort().catch(()=>{});throw e;}
  return {configured:true,saved:true,folder:root.name};
}
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

// A VPS gera um token novo sempre que o container reinicia (atualizacoes).
// Renovar a sessao sem recarregar a pagina: formularios nao podem ser perdidos.
let sessionTokenRecovery=null;
async function renewSessionToken(){
  if(!sessionTokenRecovery){
    sessionTokenRecovery=(async()=>{
      const response=await fetch('/?_contas_session_refresh='+Date.now(),{cache:'no-store'});
      if(!response.ok)throw new Error('Nao foi possivel atualizar a sessao. Reabra a pagina.');
      const html=await response.text();
      const match=html.match(/window\.__CONTAS_TOKEN__\s*=\s*("(?:\\.|[^"\\])*")\s*;/);
      if(!match)throw new Error('Sessao expirada. Reabra a pagina para continuar.');
      const updated=JSON.parse(match[1]);
      if(typeof updated!=='string'||updated.length<20)
        throw new Error('Nao foi possivel renovar a sessao.');
      token=updated;
      window.__CONTAS_TOKEN__=updated;
      return updated;
    })().finally(()=>{sessionTokenRecovery=null;});
  }
  return sessionTokenRecovery;
}
async function api(route,method='GET',data){
  const opts={method,headers:{'X-Local-Token':token}};
  if(data!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(data);}
  let response=await fetch(route,opts);
  let body=await response.json().catch(()=>({error:'Resposta inválida do servidor.'}));
  if(response.status===403&&/sess[aã]o inv[aá]lida|reabra pelo iniciador/i.test(String(body.error||''))){
    // A operacao foi rejeitada ANTES de executar, portanto repetir e seguro.
    await renewSessionToken();
    opts.headers['X-Local-Token']=token;
    response=await fetch(route,opts);
    body=await response.json().catch(()=>({error:'Resposta inválida do servidor.'}));
  }
  if(!response.ok||body.ok===false)throw new Error(body.error||'Operação não concluída.');
  return body;
}
async function postPDF(route,file,extraHeaders={}){
  const opts={
    method:'POST',
    headers:{'X-Local-Token':token,'Content-Type':'application/pdf',...extraHeaders},
    body:file
  };
  let response=await fetch(route,opts);
  if(response.status===403){
    const rejected=await response.clone().json().catch(()=>({}));
    if(/sess[aã]o inv[aá]lida|reabra pelo iniciador/i.test(String(rejected.error||''))){
      await renewSessionToken();
      opts.headers['X-Local-Token']=token;
      response=await fetch(route,opts);
    }
  }
  return response;
}
function notice(message,error=false,duration=4200){
  const toast=$('toast');toast.textContent=message;toast.className='toast '+(error?'error':'ok');toast.style.display='block';
  clearTimeout(notice.timer);notice.timer=setTimeout(()=>toast.style.display='none',duration);
}
async function reload(){
  const data=await api('/api/accounts');
  accounts=data.accounts||[];
  if(selected&&!accounts.some(a=>a.id===selected))selected=null;
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
  const requested=$('status').value;
  const query=$('search').value.trim().toLocaleLowerCase('pt-BR');
  const visible=accounts.filter(a=>{
    if(activeTab==='paid'&&a.status!=='Paga')return false;
    if(activeTab==='open'&&a.status==='Paga')return false;
    if(requested!=='all'&&a.status!==requested)return false;
    if(query&&![
      a.fornecedor,a.observacoes,a.documento,a.referencia,a.pagador,a.valor,
      a.vencimento,a.codigo_pagamento
    ].some(v=>String(v||'').toLocaleLowerCase('pt-BR').includes(query)))return false;
    return true;
  });
  $('count').textContent=visible.length+' registros';
  const body=$('rows');body.replaceChildren();
  if(!visible.length){
    const tr=document.createElement('tr'),td=document.createElement('td');
    td.colSpan=7;td.style.cssText='text-align:center;padding:48px;color:#8494a9';
    td.textContent=accounts.length?'Nenhuma conta encontrada com estes filtros.':'Nenhuma conta cadastrada.';
    tr.appendChild(td);body.appendChild(tr);return;
  }
  for(const a of visible){
    const tr=document.createElement('tr');
    tr.dataset.id=a.id;tr.tabIndex=0;
    if(selected===a.id)tr.classList.add('selected');
    tr.title='Dê dois cliques para abrir as ações da conta';
    const dueDescription=dayLabel(a.vencimento);
    tr.innerHTML=
       '<td data-label="Fornecedor"><div class="vendor"><span class="vendor-avatar" style="background:'+avatarColor(a.fornecedor)+'">'+esc(initials(a.fornecedor))+'</span><span class="vendor-name">'+esc(a.fornecedor)+'</span></div></td>'
      +'<td data-label="Observações" class="obs" title="'+esc(a.observacoes||'')+'">'+esc(a.observacoes||'—')+'</td>'
      +'<td data-label="Vencimento"><div class="due">'+esc(dateBR(a.vencimento))+'<small class="'+(dueDescription.includes('atrás')?'late':'')+'">'+esc(a.status==='Paga'?'Pago':dueDescription)+'</small></div></td>'
      +'<td data-label="Valor" class="value">'+esc(money(a.valor))+'</td>'
      +'<td data-label="Status"><span class="pill '+esc(a.status.replace(/\s/g,''))+'">'+esc(a.status)+'</span></td>'
      +'<td data-label="Sugerido em" class="date-time">'+esc(timeBR(a.suggested_at))+'</td>'
      +'<td data-label="Enviado em" class="date-time">'+esc(timeBR(a.enviado_pagamento_em))+'</td>';
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
  $('dialogBody').innerHTML=bodyHTML;$('save').textContent=label;$('save').disabled=false;
  dialogSave=onSave;$('backdrop').classList.add('show');
  $('dialogBody').querySelector('input,select,textarea')?.focus();
}
function closeModal({saved=false}={}){
  if(!$('backdrop').classList.contains('show'))return false;
  // Nao descartar um cadastro/edicao por clique acidental fora do formulario,
  // Escape ou Cancelar. A previa OCR so expira quando a saida for confirmada.
  if(!saved&&$('accountForm')&&!window.confirm(
    'Deseja realmente abandonar este formulario? As informacoes preenchidas serao perdidas.'
  ))return false;
  $('backdrop').classList.remove('show');dialogSave=null;
  const ticket=activePreviewTicket;activePreviewTicket=null;
  if(ticket&&!saved)api('/api/import/cancel','POST',{ticket}).catch(()=>{});
  if(ticket&&pendingImportPDF?.ticket===ticket)pendingImportPDF=null;
  return true;
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
    (analysis?warning+additional+modeNote:'')+
    ((preview?.ticket||(a&&a.has_pdf))
      ? '<div class="gpt-correction"><button type="button" id="correctGPT" class="outline">✦ Corrigir com GPT</button>'+
        '<span id="gptCorrectionMessage" role="status">Opcional. Só usa sua cota quando você clicar. Confira as sugestões antes de salvar.</span></div>'
      : '')+
    '<form id="accountForm" class="formgrid">'+form+'</form>',
    preview?'Confirmar e salvar PDF':a?'Salvar alterações':'Criar conta',async()=>{
      const form=$('accountForm');
      if(!form.reportValidity())return false;
      const values=Object.fromEntries(new FormData(form));
      // Solicitar permissao no clique ANTES da requisicao ao servidor.
      if(preview?.ticket&&pendingImportPDF?.ticket===preview.ticket)
        await prepareWindowsFolderCopy();
      const route=preview?'/api/import/commit':a?'/api/accounts/'+a.id+'/edit':'/api/accounts';
      const saved=await api(route,'POST',preview?{...values,ticket:preview.ticket}:values);
      let localCopy=null;
      if(preview?.ticket&&pendingImportPDF?.ticket===preview.ticket){
        localCopy=await copyOrQueueWindowsPDF(
          pendingImportPDF.file,values,saved.id||saved.account_id
        );
        pendingImportPDF=null;
      }
      if(saved.warning){
        // Cadastro foi salvo, mas a correcao ainda nao ensinou o leitor.
        // Aviso nao pode desaparecer dentro do retorno JSON.
        notice(saved.warning,true,10000);
      }else if(preview){
        const trained=(saved.learning?.fields||[]).length;
        notice(trained?'Conta salva. Aprendizado OCR registrado ('+trained+' campos).':
          'Conta salva. Nenhum novo campo foi aprendido.');
      }else notice(a?'Conta atualizada.':'Conta cadastrada.');
      if(localCopy?.saved&&!saved.warning)
        notice('Conta salva. PDF também copiado para a pasta do Windows.');
      else if(localCopy?.queued)
        notice('Conta salva na VPS. Cópia do Windows pendente: abra Pasta boletos e clique em Copiar pendentes.',true,14000);
      else if(localCopy?.error)
        notice('Conta salva na VPS, mas SEM cópia local: '+localCopy.error,true,14000);
      // Salvar/importar nao altera o filtro escolhido pelo usuario.
      await reload();return true;
    });
  if($('correctGPT')){
    $('correctGPT').addEventListener('click',async()=>{
      const button=$('correctGPT'),form=$('accountForm');
      if(!aiStatus.connected){
        $('gptCorrectionMessage').textContent='Autorize a conexão na aba do ChatGPT. Depois clique novamente em Corrigir com GPT; o formulário será preservado.';
        $('aiConnect').click(); // o login pode iniciar sem fechar o formulario
        return;
      }
      if(!aiStatus.enabled){
        if(!window.confirm('Habilitar correção manual com GPT? Nenhum PDF será enviado até você clicar novamente para corrigir.'))return;
        try{
          await api('/api/ai/consent','POST',{enabled:true});
          await refreshAI();
        }catch(e){notice(e.message||String(e),true);return;}
        if(!aiStatus.enabled)return;
      }
      const oldText=button.textContent;
      button.disabled=true;$('save').disabled=true;
      button.textContent='Conferindo PDF com GPT…';
      $('gptCorrectionMessage').textContent='Aguarde a análise. Nenhum dado será salvo automaticamente.';
      try{
        const current=Object.fromEntries(new FormData(form));
        const request=preview?.ticket
          ? {ticket:preview.ticket,fields:current}
          : {account_id:a.id,fields:current};
        const corrected=await api('/api/ai/correct','POST',request);
        // Se o usuario fechou/trocou o formulario durante a leitura,
        // nao permitir que resposta tardia modifique outro cadastro.
        if($('accountForm')!==form||!$('backdrop').classList.contains('show'))return;
        let applied=0;
        for(const key of corrected.changed||[]){
          if(!accountFields.some(item=>item[0]===key))continue;
          const input=Array.from(form.elements).find(item=>item.name===key);
          if(!input)continue;
          const value=corrected.fields?.[key];
          if(value===null||value===undefined||String(value).trim()==='')continue;
          input.value=String(value);
          input.classList.add('gpt-corrected');
          applied++;
        }
        const message=applied
          ? 'GPT sugeriu alterações em '+applied+' campo(s). Confira os valores e clique em Salvar para treinar o OCR.'
          : 'GPT não encontrou correções seguras. Confira os dados manualmente.';
        $('gptCorrectionMessage').textContent=message;
        notice(message,!applied,8200);
      }catch(e){
        if($('accountForm')===form){
          $('gptCorrectionMessage').textContent='Não foi possível corrigir com GPT. Seus dados permanecem como estavam.';
          notice(e.message||String(e),true,9000);
        }
      }finally{
        if($('accountForm')===form){
          button.disabled=false;button.textContent=oldText;$('save').disabled=false;
        }
      }
    });
  }
}
function payModal(a){
  if(a.status==='Paga'){notice('Conta já paga.',true);return}
  const now=localToday();
  let checkingReceipt=false,receiptResult=null;
  showModal('Registrar pagamento',a.fornecedor+' · '+money(a.valor),
    '<label class="field">Data do pagamento<input type="date" id="paidDate" value="'+now+'"></label>'
    +'<label class="field" style="margin-top:15px">Comprovante PDF (opcional)<input type="file" id="receiptFile" accept=".pdf,application/pdf"></label>'
    +'<div class="receipt-check" id="receiptCheck" aria-live="polite"></div>'
    +'<p class="hint">O aplicativo confere os identificadores antes de mesclar o comprovante. Se existirem contas idênticas sem prova exclusiva, será necessária confirmação manual. Sem comprovante: registro manual.</p>',
    'Confirmar pagamento',async()=>{
      const paidOn=$('paidDate').value;
      if(!paidOn)throw new Error('Escolha a data.');
      const proof=$('receiptFile').files[0];
      if(proof){
        if(checkingReceipt)throw new Error('Aguarde a conferência do comprovante.');
        if(proof.size>30*1024*1024)throw new Error('Comprovante acima de 30 MB.');
        if(receiptResult?.status==='conflict')
          throw new Error(receiptResult.reason||'Comprovante identificado como pertencente a outra conta.');
        const checked=Boolean($('receiptManualConfirm')?.checked);
        if(receiptResult?.manual_required&&!checked)
          throw new Error('Confira a conta e marque a confirmação manual antes de registrar.');
        const response=await fetch('/api/accounts/'+a.id+'/pay-with-receipt',{
          method:'POST',headers:{'X-Local-Token':token,
            'Content-Type':'application/pdf','X-Paid-Date':paidOn,
            'X-Receipt-Manual-Confirmed':checked?'yes':'no'},body:proof
        });
        const result=await response.json().catch(()=>({error:'Resposta de pagamento inválida.'}));
        if(!response.ok||!result.ok)throw new Error(result.error||'Pagamento não registrado.');
        notice('Pagamento registrado. Comprovante mesclado ao PDF ('+result.pages+' páginas).');
      }else{
        await api('/api/accounts/'+a.id+'/pay','POST',{data_pagamento:paidOn});
        notice('Pagamento manual registrado sem comprovante.');
      }
      // O pagamento muda o status da conta, nunca o filtro da tela.
      await reload();return true;
    });
  $('receiptFile').addEventListener('change',async()=>{
    const proof=$('receiptFile').files?.[0],status=$('receiptCheck');
    receiptResult=null;
    status.replaceChildren();
    if(!proof)return;
    if(proof.size>30*1024*1024){status.textContent='Comprovante acima de 30 MB.';return;}
    checkingReceipt=true;
    status.textContent='Conferindo identificadores do comprovante...';
    try{
      const response=await fetch('/api/accounts/'+a.id+'/verify-receipt',{
        method:'POST',headers:{'X-Local-Token':token,
          'Content-Type':'application/pdf'},body:proof
      });
      const answer=await response.json().catch(()=>({error:'Falha na leitura da conferência.'}));
      if($('receiptFile').files?.[0]!==proof||!$('backdrop').classList.contains('show'))return;
      if(!response.ok||!answer.ok)throw new Error(answer.error||'Não foi possível conferir o PDF.');
      receiptResult=answer;
      status.replaceChildren();
      const message=document.createElement('p');
      message.textContent=answer.status==='verified'
        ? 'Identificador confirmado: o comprovante corresponde a esta conta.'
        : answer.reason;
      message.className=answer.status==='verified'?'receipt-success':
        answer.status==='conflict'?'receipt-error':'receipt-warning';
      status.appendChild(message);
      if(answer.manual_required){
        const detail=document.createElement('small');
        detail.textContent='Contas semelhantes: '+(answer.candidate_ids||[]).join(', ')+'.';
        status.appendChild(detail);
        const label=document.createElement('label');
        label.className='receipt-manual';
        const box=document.createElement('input');
        box.id='receiptManualConfirm';box.type='checkbox';
        label.appendChild(box);
        label.appendChild(document.createTextNode(
          ' Conferi o comprovante e confirmo que pertence à conta '+a.id+'.'
        ));
        status.appendChild(label);
      }
    }catch(e){
      if($('receiptFile').files?.[0]===proof){
        status.replaceChildren();
        const msg=document.createElement('p');
        msg.className='receipt-error';
        msg.textContent=e.message||String(e);
        status.appendChild(msg);
      }
    }finally{checkingReceipt=false;}
  });
}

function isReceiptFilename(name){
  // Apenas roteamento de importacao. O nome nao comprova a identidade da conta.
  return /comprovante|pagamento/i.test(String(name||''));
}
let receiptImportBusy=false;
function confirmEligibleReceipt(file, report){
  const ids=new Set((report.candidate_ids||[]).map(Number));
  const eligible=accounts.filter(a=>ids.has(a.id)&&a.status!=='Paga')
    .sort((a,b)=>a.id-b.id);
  if(!eligible.length)throw new Error('Nenhuma conta em aberto elegível.');
  const options=eligible.map(a=>
    '<label class="receipt-eligible-option">'+
      '<input type="radio" name="receiptEligible" value="'+a.id+'"'+
        (eligible.length===1?' checked':'')+'>'+
      '<span class="receipt-eligible-details">'+
        '<strong>Conta #'+a.id+' — '+esc(a.fornecedor)+'</strong>'+
        '<span>'+esc(money(a.valor))+' · vence '+esc(dateBR(a.vencimento))+'</span>'+
        '<span>Observações: '+esc(a.observacoes||'—')+'</span>'+
        (a.pagador?'<span>Pagador: '+esc(a.pagador)+'</span>':'')+
      '</span></label>'
  ).join('');
  const date=report.payment_date||localToday();
  const content=
    '<div class="receipt-drop-status receipt-warning">'+
      eligible.length+' conta(s) elegível(is) por '+
      (report.receipt_type==='darf'?'número do DARF ou código de barras':
       'recebedor, valor e vencimento')+
      '. Confira e escolha a correta.</div>'+
    '<div class="receipt-eligible-list">'+options+'</div>'+
    '<label class="field" style="margin-top:12px">Data do pagamento'+
      '<input type="date" id="eligiblePaidOn" value="'+esc(date)+'"></label>'+
    '<p class="hint">Nenhuma conta será paga ou terá o PDF alterado até você confirmar.</p>';
  showModal('Conferir comprovante',file.name,content,
    'Confirmar pagamento e anexar',async()=>{
      const chosen=document.querySelector('input[name="receiptEligible"]:checked');
      const id=Number(chosen?.value||0);
      if(!ids.has(id)||!eligible.some(a=>a.id===id))
        throw new Error('Selecione uma das contas elegíveis.');
      const paidOn=$('eligiblePaidOn').value;
      if(!paidOn)throw new Error('Informe a data do pagamento.');
      // O servidor reconfere recebedor, valor, vencimento e duplicidade
      // antes de mesclar qualquer PDF ou criar o pagamento.
      const result=await fetch('/api/accounts/'+id+'/pay-with-receipt',{
        method:'POST',
        headers:{'X-Local-Token':token,'Content-Type':'application/pdf',
                 'X-Paid-Date':paidOn,'X-Receipt-Eligible-Confirmed':'yes'},
        body:file
      });
      const saved=await result.json().catch(()=>({error:'Resposta inválida do pagamento.'}));
      if(!result.ok||!saved.ok)
        throw new Error(saved.error||'Não foi possível registrar o pagamento.');
      selected=id;
      // Comprovante identificado nao troca a visualizacao atual.
      await reload();
      notice('Comprovante anexado à conta #'+id+'. Pagamento registrado.');
      return true;
    });
}
async function importDroppedReceipt(file){
  if(!file)return;
  if($('backdrop').classList.contains('show')){
    notice('Conclua a ação atual antes de importar outro PDF.',true);return;
  }
  if(importBusy||receiptImportBusy){
    notice('Já existe um PDF em processamento.',true);return;
  }
  if(!/\.pdf$/i.test(file.name||'')){
    notice('Arraste um comprovante em PDF.',true);return;
  }
  if(file.size>30*1024*1024){
    notice('O comprovante PDF deve ter até 30 MB.',true);return;
  }
  receiptImportBusy=true;
  notice('Procurando contas elegíveis...',false,7000);
  try{
    const response=await fetch('/api/receipts/eligible',{
      method:'POST',headers:{'X-Local-Token':token,'Content-Type':'application/pdf'},
      body:file
    });
    const report=await response.json().catch(()=>({error:'Resposta inválida da conferência.'}));
    if(!response.ok||!report.ok)
      throw new Error(report.error||'Não foi possível ler o comprovante.');
    if(report.status!=='eligible'||!report.candidate_ids?.length)
      throw new Error('Nenhuma conta elegível: '+(report.reason||'não foi possível conferir os dados.'));
    await reload();
    confirmEligibleReceipt(file,report);
  }catch(error){
    notice(error.message||String(error),true,10500);
  }finally{
    receiptImportBusy=false;
  }
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
  await prepareWindowsFolderCopy();
  const response=await postPDF('/api/accounts/'+selected+'/upload-pdf',file,{
    'X-Pdf-Name':encodeURIComponent(file.name)
  });
  const data=await response.json().catch(()=>({error:'Falha ao enviar PDF.'}));
  if(!response.ok||!data.ok)throw new Error(data.error||'Falha ao importar PDF.');
  const registered=accounts.find(item=>item.id===selected)||{};
  const savedLocal=await copyOrQueueWindowsPDF(file,registered,selected);
  notice(savedLocal?.saved?'PDF anexado e cópia salva na pasta do Windows.':
    savedLocal?.queued?'PDF anexado na VPS. Cópia Windows pendente: abra Pasta boletos e clique em Copiar pendentes.':
    savedLocal?.error?'PDF anexado na VPS, mas SEM cópia no Windows: '+savedLocal.error:
    'PDF anexado à VPS. Configure Pasta boletos para copiar ao Windows.',
    Boolean(savedLocal?.error||savedLocal?.queued),14000);
  await reload();
}

/* Desktop: colunas personalizadas, sem redimensionar o texto das celulas. */
const COL_WEIGHT=[25,25,12,12,10,8,8],COL_MIN=[160,170,107,105,96,100,100];
const COL_KEY='controle-contas-colunas-v1';
const table=document.querySelector('.table-scroll table');
const tableArea=document.querySelector('.table-scroll');
const cols=Array.from(table.querySelectorAll('colgroup col'));
const heads=Array.from(table.querySelectorAll('thead th'));
let widths=null,customWidths=false;
function autoColumnWidths(){
  const area=Math.max(1,tableArea.clientWidth-2);
  const extra=Math.max(0,area-COL_MIN.reduce((a,b)=>a+b,0));
  const w=COL_MIN.map((minimum,i)=>minimum+Math.floor(extra*COL_WEIGHT[i]/100));
  w[w.length-1]+=Math.max(0,area-w.reduce((a,b)=>a+b,0));
  return w;
}
function readColumnWidths(){
  try{
    const x=JSON.parse(window.localStorage.getItem(COL_KEY)||'null');
    if(Array.isArray(x)&&x.length===COL_MIN.length&&x.every((n,i)=>Number.isFinite(n)&&n>=COL_MIN[i]&&n<=1500))return x;
  }catch(_){}
  return null;
}
function applyColumnLayout(){
  // A vista compacta usa cards: zerar larguras inline do desktop para
  // impedir que a tabela continue larga e corte os cards em telas pequenas.
  const desktop=window.innerWidth>990;
  cols.forEach((col,i)=>{col.style.width=desktop?widths[i]+'px':'';});
  table.style.width=desktop?widths.reduce((a,b)=>a+b,0)+'px':'';
  heads.forEach((th,i)=>th.querySelector('.col-resize')?.setAttribute('aria-valuenow',String(widths[i])));
}
function setColumnWidths(next){
  widths=next.map((n,i)=>Math.max(COL_MIN[i],Math.min(1500,Math.round(n))));
  applyColumnLayout();
}
function persistColumnWidths(){
  try{window.localStorage.setItem(COL_KEY,JSON.stringify(widths));}catch(_){}
}
function changeColumnWidth(i,next){
  if(window.innerWidth<=990)return;
  const data=widths.slice();
  data[i]=Math.max(COL_MIN[i],Math.min(1500,Math.round(next)));
  setColumnWidths(data);customWidths=true;
}
function resetColumnWidths(){
  customWidths=false;
  try{window.localStorage.removeItem(COL_KEY);}catch(_){}
  if(window.innerWidth>990)setColumnWidths(autoColumnWidths());
}
function initializeColumnResizing(){
  const saved=readColumnWidths();customWidths=Boolean(saved);
  setColumnWidths(saved||autoColumnWidths());
  heads.forEach((th,i)=>{
    const handle=document.createElement('span');
    handle.className='col-resize';handle.tabIndex=0;
    handle.setAttribute('role','separator');
    handle.setAttribute('aria-orientation','vertical');
    handle.setAttribute('aria-label','Largura da coluna '+th.textContent.trim());
    handle.setAttribute('aria-valuemin',String(COL_MIN[i]));
    handle.setAttribute('aria-valuemax','1500');
    handle.title='Arraste para ajustar; duplo clique para restaurar o padrão';
    th.appendChild(handle);
    let active=false,initialX=0,initialWidth=0;
    handle.addEventListener('pointerdown',e=>{
      if(e.button!==0||window.innerWidth<=990)return;
      e.preventDefault();e.stopPropagation();
      active=true;initialX=e.clientX;initialWidth=widths[i];
      handle.classList.add('active');document.body.classList.add('resizing-column');
      handle.setPointerCapture?.(e.pointerId);
    });
    handle.addEventListener('pointermove',e=>{
      if(active){e.preventDefault();changeColumnWidth(i,initialWidth+e.clientX-initialX);}
    });
    const stop=e=>{
      if(!active)return;
      active=false;handle.classList.remove('active');document.body.classList.remove('resizing-column');
      if(handle.hasPointerCapture?.(e.pointerId))handle.releasePointerCapture(e.pointerId);
      persistColumnWidths();
    };
    handle.addEventListener('pointerup',stop);
    handle.addEventListener('pointercancel',stop);
    handle.addEventListener('lostpointercapture',stop);
    handle.addEventListener('dblclick',e=>{e.preventDefault();e.stopPropagation();resetColumnWidths();});
    handle.addEventListener('keydown',e=>{
      let next;
      if(e.key==='ArrowLeft')next=widths[i]-20;
      else if(e.key==='ArrowRight')next=widths[i]+20;
      else if(e.key==='Home')next=COL_MIN[i];
      else return;
      e.preventDefault();e.stopPropagation();
      changeColumnWidth(i,next);persistColumnWidths();
    });
  });
  setColumnWidths(widths);
  $('resetColumns').addEventListener('click',resetColumnWidths);
  window.addEventListener('resize',()=>{
    if(window.innerWidth>990&&!customWidths)setColumnWidths(autoColumnWidths());
    else applyColumnLayout();
  });
}
initializeColumnResizing();


/* A IA e opcional e fica desligada ate login e autorizacao explicita.
   Nenhum dado financeiro sai do PC antes do consentimento. */
let aiStatus={connected:false,enabled:false,consented:false};
async function refreshAI(){
  try{
    aiStatus=await api('/api/ai/status');
    const button=$('aiConnect');
    button.textContent=aiStatus.enabled?'ChatGPT conectado':aiStatus.connected?'IA desativada':'Conectar ChatGPT';
    button.classList.toggle('enabled',Boolean(aiStatus.enabled));
    button.classList.toggle('disabled',Boolean(aiStatus.connected&&!aiStatus.enabled));
    button.title=aiStatus.enabled?'GPT disponível somente pelo botão Corrigir com GPT no formulário':
      'A IA é opcional e nunca roda automaticamente.';
  }catch(_){
    // A interface de contas nao depende da disponibilidade deste recurso.
  }
}
$('aiConnect').addEventListener('click',async()=>{
  // Login pode ser iniciado a partir do formulario sem apagar dados digitados.
  // Apenas a janela de configuracoes nao deve substituir um modal aberto.
  if($('backdrop').classList.contains('show')&&aiStatus.connected)return;
  if(aiStatus.connected){
    showModal('Assistência ChatGPT','O OCR continua sendo o leitor principal.',
      '<div class="ai-settings"><label><input id="aiEnabled" type="checkbox" '+
      (aiStatus.enabled?'checked':'')+
      '> Permitir correção manual com GPT nos formulários</label>'+
      '<p>Nenhum PDF será enviado automaticamente. A IA só revisa dados quando você clicar em Corrigir com GPT '+
      'dentro de um formulário com PDF. Confira as sugestões e salve para treinar o OCR. '+
      'A correção manual pode substituir campos preenchidos incorretamente. '+
      'O uso consome a cota autorizada do seu ChatGPT, sem chave API.</p>'+
      '<button type="button" id="disconnectAI" class="outline">Desconectar ChatGPT</button></div>',
      'Salvar configuração',async()=>{
        await api('/api/ai/consent','POST',{enabled:$('aiEnabled').checked});
        await refreshAI();return true;
      });
    $('disconnectAI').addEventListener('click',async()=>{
      if(!window.confirm('Desconectar o ChatGPT? O OCR continuará funcionando normalmente.'))return;
      try{
        await api('/api/ai/disconnect','POST',{});
        closeModal({saved:true});
        await refreshAI();
        notice('ChatGPT desconectado. OCR local preservado.');
      }catch(e){notice(e.message||String(e),true)}
    });
    return;
  }
  if(!window.confirm('Conectar ChatGPT para CORRIGIR BOLETOS SOMENTE QUANDO VOCÊ CLICAR em Corrigir com GPT? Nenhum PDF será enviado automaticamente. Cada correção solicitada enviará imagens do documento à OpenAI usando a cota autorizada do seu plano. Nenhuma API paga será configurada.'))return;
  const tab=window.open('about:blank','_blank');
  try{
    await api('/api/ai/consent','POST',{enabled:true});
    const result=await api('/api/ai/connect','POST',{});
    if(tab)tab.location.replace(result.url);
    else window.open(result.url,'_blank');
    notice('Autorize a conexão na aba do ChatGPT e volte ao aplicativo.',false,9000);
    let checks=0;
    const watch=setInterval(async()=>{
      checks++;
      await refreshAI();
      if(aiStatus.connected||checks>=48)clearInterval(watch);
    },2500);
  }catch(e){
    if(tab&&!tab.closed)tab.close();
    notice('Não foi possível iniciar conexão: '+(e.message||String(e)),true,9500);
  }
});
window.addEventListener('focus',refreshAI);
refreshAI();

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
    const response=await postPDF('/api/import/preview',file,{
      'X-Pdf-Name':encodeURIComponent(file.name),'X-Ocr-Mode':'hybrid'
    });
    const result=await response.json().catch(()=>({error:'Resposta inválida do leitor OCR.'}));
    if(!response.ok||!result.ok)throw new Error(result.error||'Não foi possível ler o PDF.');
    pendingImportPDF={ticket:result.ticket,file};
    accountModal(null,result);
  }catch(err){notice(err.message||String(err),true);}
  finally{importBusy=false;button.disabled=false;button.innerHTML=previous;}
}
$('importFile').addEventListener('change',e=>{
  const file=e.target.files[0];e.target.value='';
  if(file)importNewPDF(file);
});
// O nome define somente o fluxo: comprovante procura conta; boleto continua cadastro.
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
  if(isReceiptFilename(files[0].name))importDroppedReceipt(files[0]);
  else importNewPDF(files[0]);
});
window.addEventListener('blur',hideDropZone);
$('uploadFile').addEventListener('change',async e=>{
  const file=e.target.files[0];if(file){try{await uploadAccountPDF(file)}catch(err){notice(err.message,true)}}
  e.target.value='';
});
$('folderBtn').addEventListener('click',async()=>{
  try{
    const current=await readWindowsBoletoFolder();
    showModal('Pasta de boletos do Windows',
      'A pasta fica lembrada neste navegador. O Edge/Chrome pode exigir nova autorizacao ao fechar e reabrir o site.',
      '<div class="field wide"><b>Pasta selecionada neste computador</b>'+
      '<div id="windowsFolderName" style="padding:12px;border:1px solid #dbe5f0;border-radius:8px;margin:7px 0 8px">'+
      esc(current?.name||'Nenhuma pasta selecionada')+'</div>'+
      '<p id="windowsFolderStatus" class="hint">Verificando a pasta...</p>'+
      '<p id="windowsCopyQueueStatus" class="hint"></p></div>'+
      '<button type="button" id="authorizeWindowsFolder" class="outline">Autorizar pasta salva</button> '+
      '<button type="button" id="browseWindowsFolder" class="outline">'+(current?'Trocar pasta…':'Selecionar pasta…')+'</button> '+
      '<button type="button" id="testWindowsFolder" class="outline">Testar gravação</button> '+
      '<button type="button" id="retryWindowsCopies" class="outline">Copiar pendentes</button>'+
      '<p class="hint">Selecione a pasta BOLETOS principal, nao uma subpasta de ano/mes. '+
      'O teste cria e remove um arquivo temporario, sem alterar seus boletos. '+
      'Se a autorizacao do navegador expirar, clique em Autorizar pasta salva. '+
      'PDFs pendentes sao guardados somente neste navegador ate serem copiados.</p>',
      'Concluir',async()=>{
        const root=await readWindowsBoletoFolder();
        if(!root)throw new Error('Selecione a pasta BOLETOS pela primeira vez.');
        validateWindowsFolderRoot(root);
        const permission=await windowsFolderPermission(root);
        if(permission!=='granted')
          throw new Error('Pasta lembrada, mas sem acesso. Clique em Autorizar pasta salva.');
        await testWindowsFolderWrite(root);
        return true;
      });
    async function updateFolderStatus(){
      const root=await readWindowsBoletoFolder();
      const text=$('windowsFolderStatus'),button=$('authorizeWindowsFolder');
      const retry=$('retryWindowsCopies'),queue=$('windowsCopyQueueStatus');
      if(!text||!button||!retry||!queue)return;
      let permission='not-configured';
      try{permission=await windowsFolderPermission(root);}
      catch(e){permission='error';}
      text.textContent=!root?'Nenhuma pasta selecionada. Escolha a pasta BOLETOS principal.':
        permission==='granted'?'Pasta lembrada e autorizada para gravar.':
        permission==='prompt'?'Pasta lembrada, mas o navegador precisa de sua autorizacao.':
        'A pasta esta lembrada, mas o acesso foi negado ou nao esta disponivel.';
      button.disabled=!root||permission==='granted';
      const pending=await windowsPendingCopies();
      queue.textContent=pending.length
        ?pending.length+' PDF(s) aguardando copia no Windows. Use Copiar pendentes.'
        :'Nenhum PDF aguardando copia no Windows neste navegador.';
      retry.disabled=!root||!pending.length;
      $('testWindowsFolder').disabled=!root;
    }
    $('authorizeWindowsFolder').addEventListener('click',async()=>{
      try{
        const root=windowsFolderHandle||await readWindowsBoletoFolder();
        if(!root)throw new Error('Selecione a pasta BOLETOS primeiro.');
        validateWindowsFolderRoot(root);
        const state=await windowsFolderPermission(root,true);
        if(state!=='granted')throw new Error('Autorize o acesso a pasta na janela do navegador.');
        await testWindowsFolderWrite(root);
        await updateFolderStatus();
        notice('Pasta '+root.name+' autorizada. Teste real de gravacao concluido.');
      }catch(e){notice('Nao foi possivel autorizar: '+(e.message||String(e)),true,12000);}
    });
    $('browseWindowsFolder').addEventListener('click',async()=>{
      try{
        if(typeof window.showDirectoryPicker!=='function')
          throw new Error('Use Microsoft Edge ou Google Chrome atualizado no Windows.');
        // O seletor deve ser aberto diretamente pelo clique do usuario.
        const chosen=await window.showDirectoryPicker({id:'contas-boletos',mode:'readwrite'});
        validateWindowsFolderRoot(chosen);
        await rememberWindowsBoletoFolder(chosen);
        $('windowsFolderName').textContent=chosen.name;
        $('browseWindowsFolder').textContent='Trocar pasta…';
        await updateFolderStatus();
        notice('Pasta '+chosen.name+' lembrada. Clique em Testar gravacao para conferir.');
      }catch(e){if(e?.name!=='AbortError')notice(e.message||String(e),true,12000);}
    });
    $('testWindowsFolder').addEventListener('click',async()=>{
      try{
        const root=windowsFolderHandle||await readWindowsBoletoFolder();
        if(!root)throw new Error('Selecione a pasta BOLETOS primeiro.');
        const permission=await windowsFolderPermission(root,true);
        if(permission!=='granted')throw new Error('Autorize a pasta na janela do navegador.');
        await testWindowsFolderWrite(root);
        await updateFolderStatus();
        notice('Teste OK: o navegador consegue gravar na pasta '+root.name+'.');
      }catch(e){notice('Teste da pasta falhou: '+(e.message||String(e)),true,12000);}
    });
    $('retryWindowsCopies').addEventListener('click',async()=>{
      const button=$('retryWindowsCopies');
      button.disabled=true;
      try{
        const copied=await copyPendingWindowsPDFs();
        await updateFolderStatus();
        notice(copied+' PDF(s) copiado(s) ao Windows. Nenhuma conta da VPS foi alterada.');
      }catch(e){notice('Copia pendente nao concluida: '+(e.message||String(e)),true,15000);}
      finally{await updateFolderStatus().catch(()=>{button.disabled=false;});}
    });
    await updateFolderStatus();
  }catch(e){notice(e.message||String(e),true,12000);}
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
$('cancel').addEventListener('click',()=>closeModal());
$('backdrop').addEventListener('click',e=>{if(e.target===$('backdrop'))closeModal()});
$('save').addEventListener('click',async()=>{
  if(!dialogSave)return;
  $('save').disabled=true;
  try{const ok=await dialogSave();if(ok)closeModal({saved:true})}
  catch(e){notice(e.message||String(e),true)}
  finally{$('save').disabled=false}
});
reload().catch(e=>notice('Não foi possível carregar o banco: '+e.message,true));
setInterval(()=>{
  if(!$('backdrop').classList.contains('show')&&!$('menu').classList.contains('visible'))
    reload().catch(()=>{});
},15000);
