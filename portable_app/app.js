/* Interface portátil: nenhuma dependência de framework, sem banco no navegador. */
'use strict';
const $=id=>document.getElementById(id);
let token=window.__CONTAS_TOKEN__;
let accounts=[], selected=null, activeTab='open', dialogSave=null;
let payrollEmployees=[], moduleActive='contas';
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
// Uma cópia falhada continua pendente ao fechar o navegador.
// A fila guarda somente o ID e o destino; o PDF final é sempre
// recuperado da VPS na tentativa automática seguinte.
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
  if(!Number.isInteger(Number(id))||Number(id)<=0)
    throw new Error('Nao foi possivel identificar a conta para repetir a copia.');
  return windowsCopyTransaction('readwrite',store=>store.put({
    id:Number(id),details:{
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
async function ensureAutomaticWindowsFolder(){
  // O navegador exige autorização na primeira vez que grava em C:\.
  // Depois reutiliza a pasta persistida no IndexedDB, sem sincronização.
  if(!/Windows/i.test(navigator.userAgent||''))return null;
  let root=await readWindowsBoletoFolder();
  if(!root){
    if(typeof window.showDirectoryPicker!=='function')
      throw new Error('Para salvar automaticamente no Windows, abra o CONTAS 4 pelo Edge/Chrome e autorize a pasta de boletos.');
    root=await window.showDirectoryPicker({mode:'readwrite'});
    validateWindowsFolderRoot(root);
    await rememberWindowsBoletoFolder(root);
  }
  return (await prepareWindowsFolderCopy())||root;
}
async function fetchFinalPdfFromServer(id){
  const request=()=>fetch('/api/accounts/'+Number(id)+'/pdf',{
    headers:{'X-Local-Token':token},cache:'no-store'
  });
  let response=await request();
  if(response.status===403){
    const detail=await response.clone().json().catch(()=>({}));
    if(/sess[aã]o inv[aá]lida|reabra pelo iniciador/i.test(String(detail.error||''))){
      await renewSessionToken();response=await request();
    }
  }
  if(!response.ok){
    const result=await response.json().catch(()=>({}));
    throw new Error(result.error||'A VPS não retornou o PDF final.');
  }
  const body=await response.blob();
  if(body.type!=='application/pdf'||body.size<10)
    throw new Error('O PDF final da VPS está inválido.');
  return body;
}
// Todas as gravações usam uma fila serial no navegador, evitando que uma
// reconciliação antiga sobrescreva o comprovante recém-mesclado de uma conta.
let windowsWriteTail=Promise.resolve(),windowsReconcileRunning=false;
let windowsLastReconcile=0;
function serializeWindowsWrite(task){
  const pending=windowsWriteTail.catch(()=>{}).then(task);
  windowsWriteTail=pending.catch(()=>{});
  return pending;
}
async function autoSaveFinalPdfToWindows(id,info){
  if(!/Windows/i.test(navigator.userAgent||''))return {skipped:true};
  return serializeWindowsWrite(async()=>{
    try{
      const pdf=await fetchFinalPdfFromServer(id);
      const result=await copyOrQueueWindowsPDF(pdf,info,id);
      if(result?.saved){
        await removeWindowsCopyPending(id).catch(()=>{});
      }else{
        await saveWindowsCopyPending(null,info,id);
      }
      return result;
    }catch(error){
      // O cadastro/pagamento já foi gravado na VPS. Apenas a cópia Windows
      // foi adiada. Nunca pedir para registrar o mesmo pagamento outra vez.
      await saveWindowsCopyPending(null,info,id).catch(()=>{});
      return {saved:false,queued:true,error:error.message||String(error)};
    }
  });
}
async function windowsExistingPDF(folder,id){
  for await(const [name,handle] of folder.entries()){
    if(handle.kind==='file'&&
       name.startsWith(String(id)+' - ')&&
       name.toLowerCase().endsWith('.pdf'))return handle;
  }
  return null;
}
async function windowsFileSHA256(handle){
  const file=await handle.getFile();
  const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
  return Array.from(new Uint8Array(digest),
    value=>value.toString(16).padStart(2,'0')).join('');
}
async function windowsAlreadyCurrent(root,item){
  const match=/^(20\d{2})-(0[1-9]|1[0-2])-\d{2}$/.exec(
    String(item.vencimento||'')
  );
  if(!match)return false;
  let folder;
  try{
    folder=await root.getDirectoryHandle('BOLETOS '+match[1]);
    folder=await folder.getDirectoryHandle(
      match[2]+' - '+WINDOWS_MONTHS[Number(match[2])-1]);
  }catch(error){
    if(error.name==='NotFoundError')return false;
    throw error;
  }
  const existing=await windowsExistingPDF(folder,item.id);
  if(!existing)return false;
  // Somente considerar sincronizado quando o hash dos bytes conferir.
  // Tamanho/nome iguais não bastam: PDFs substituídos podem ser diferentes.
  if(!/^[0-9a-f]{64}$/i.test(String(item.sha256||'')))return false;
  return (await windowsFileSHA256(existing)).toLowerCase()===
    String(item.sha256).toLowerCase();
}
async function reconcileAllWindowsPDFs(force=false){
  if(!/Windows/i.test(navigator.userAgent||'')||windowsReconcileRunning)return;
  if(!force&&Date.now()-windowsLastReconcile<45000)return;
  const root=await readWindowsBoletoFolder();
  // Pasta precisa ter sido escolhida uma única vez. Se o navegador revogar
  // a permissão, somente um novo clique do usuário pode reativá-la.
  if(!root||await windowsFolderPermission(root)!=='granted')return;
  windowsReconcileRunning=true;
  try{
    const manifest=await api('/api/windows/pdf-manifest');
    let recovered=0,failed=0;
    for(const item of manifest.items||[]){
      if(!Number.isInteger(item.id)||item.id<=0)continue;
      try{
        // A verificação e a eventual gravação ficam serializadas com os
        // uploads normais. Baixar SEMPRE a versão atual da VPS.
        const copied=await serializeWindowsWrite(async()=>{
          if(await windowsAlreadyCurrent(root,item))return false;
          const content=await fetchFinalPdfFromServer(item.id);
          const copied=await copyPDFToWindowsFolder(content,item,item.id);
          if(!copied?.saved)throw new Error(copied?.error||'Pasta indisponível.');
          return true;
        });
        await removeWindowsCopyPending(item.id).catch(()=>{});
        if(copied)recovered++;
      }catch(error){
        failed++;
        await saveWindowsCopyPending(null,item,item.id).catch(()=>{});
      }
      if((recovered+failed)%25===0){
        await new Promise(resolve=>setTimeout(resolve,0));
      }
    }
    windowsLastReconcile=Date.now();
    if(recovered){
      notice(recovered+' PDF(s) recuperado(s)/atualizado(s) automaticamente na pasta do Windows.',false,11000);
    }
    if(failed){
      console.warn('CONTAS 4: PDFs não copiados nesta tentativa:',failed);
    }
    return {recovered,failed,missingSource:manifest.missing_source||0};
  }finally{
    windowsReconcileRunning=false;
  }
}
async function retryWindowsCopiesAutomatically(){
  try{await reconcileAllWindowsPDFs();}catch(error){
    console.warn('CONTAS 4: reconciliação automática adiada:',error.message||String(error));
  }
}
window.addEventListener('focus',retryWindowsCopiesAutomatically);
setInterval(retryWindowsCopiesAutomatically,60000);
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
  // Para a mesma conta, reanexar/substituir deve atualizar a cópia
  // anterior, em vez de gerar indefinidamente (1), (2), (3)...
  let name=base+'.pdf';
  if(accountId){
    for await(const [existingName,existingHandle] of monthFolder.entries()){
      if(existingHandle.kind==='file'&&
         existingName.startsWith(String(accountId)+' - ')&&
         existingName.toLowerCase().endsWith('.pdf')){
        name=existingName;break;
      }
    }
  }else{
    for(let i=1;i<1000;i++){
      try{await monthFolder.getFileHandle(name);name=base+' ('+i+').pdf';}
      catch(e){if(e.name==='NotFoundError')break;throw e;}
    }
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
  return distance<0?Math.abs(distance)+' dias atrás':distance===0?'Vencida hoje':distance===1?'Amanhã':'Em '+distance+' dias';
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
  const [data,payroll]=await Promise.all([
    api('/api/accounts'),api('/api/holerites')
  ]);
  accounts=data.accounts||[];
  payrollEmployees=payroll.funcionarios||[];
  if(selected&&!accounts.some(a=>a.id===selected))selected=null;
  render();
  renderPayroll();
}

function setModule(module){
  moduleActive=module==='holerites'?'holerites':'contas';
  $('contasView').hidden=moduleActive!=='contas';
  $('holeritesView').hidden=moduleActive!=='holerites';
  $('tabContas').setAttribute('aria-selected',String(moduleActive==='contas'));
  $('tabHolerites').setAttribute('aria-selected',String(moduleActive==='holerites'));
  closeMenu();
  if(moduleActive==='holerites')renderPayroll();
}
async function reloadPayroll(){
  await reload();
  if(moduleActive==='holerites')renderPayroll();
}
function employeeModal(employee=null){
  showModal(employee?'Editar funcionário':'Cadastrar funcionário',
    'A chave Pix fica salva para os próximos holerites. Alterar a chave não modifica documentos nem pagamentos anteriores.',
    '<form class="formgrid" id="employeeForm">'+
      '<label class="field wide">Nome completo *<input name="nome" type="text" maxlength="150" required value="'+esc(employee?.nome||'')+'"></label>'+
      '<label class="field wide">Chave Pix padrão<input name="chave_pix" type="text" maxlength="150" autocomplete="off" value="'+esc(employee?.chave_pix||'')+'" placeholder="CPF, telefone, e-mail ou chave aleatória"></label>'+
    '</form>',
    employee?'Salvar funcionário':'Cadastrar funcionário',async()=>{
      const form=$('employeeForm');
      if(!form.reportValidity())return false;
      const values=Object.fromEntries(new FormData(form));
      if(employee)values.id=employee.id;
      await api('/api/holerites/funcionarios','POST',values);
      await reloadPayroll();
      notice('Cadastro e chave Pix do funcionário salvos.');
      return true;
    });
}
function normalizePayrollName(name){
  return String(name||'').normalize('NFD')
    .replace(/[\u0300-\u036f]/g,'')
    .toLocaleUpperCase('pt-BR').replace(/\s+/g,' ').trim();
}
function matchPayrollEmployee(name){
  const key=normalizePayrollName(name);
  return key?payrollEmployees.find(e=>normalizePayrollName(e.nome)===key):null;
}
function startDetectedHolerite(preview){
  const hint=preview?.holerite_sugestao||{};
  if(!hint.detected){accountModal(null,preview);return;}
  setModule('holerites');
  const identified=String(hint.funcionario_sugestao||'').trim();
  const existing=matchPayrollEmployee(identified);
  if(existing?.chave_pix){
    accountModal(null,preview,null,{payroll:true,employee:existing});
    notice('Funcionário reconhecido: '+existing.nome+'. Chave Pix recuperada.',false,7000);
    return;
  }
  // A previa é descartada se o usuário cancelar este formulário.
  activePreviewTicket=preview.ticket;
  const staffOptions=payrollEmployees.map(e=>
    '<option value="'+Number(e.id)+'"'+(existing?.id===e.id?' selected':'')+'>'+
    esc(e.nome)+(e.chave_pix?' · Pix cadastrado':' · sem Pix')+'</option>'
  ).join('');
  showModal('Holerite — funcionário e Pix',
    identified?'Recebedor encontrado no holerite: '+identified+'. Confira e complete os dados.':
      'Holerite identificado. Informe o nome do recebedor que consta no documento.',
    '<form class="formgrid" id="payrollRecognitionForm">'+
      '<label class="field wide">O funcionário já está cadastrado?'+
        '<select id="payrollRecognizedExisting"><option value="">Novo funcionário</option>'+
        staffOptions+'</select></label>'+
      '<label class="field wide" id="payrollRecognitionNameField">Nome do funcionário *'+
        '<input id="payrollRecognizedName" name="nome" type="text" required maxlength="150" value="'+esc(identified)+'"></label>'+
      '<label class="field wide" id="payrollRecognitionPixField">Chave Pix de pagamento *'+
        '<input id="payrollRecognizedPix" name="chave_pix" type="text" required maxlength="150" autocomplete="off" placeholder="CPF, telefone, e-mail ou chave aleatória"></label>'+
      '<div class="wide" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'+
        '<button type="button" class="outline" id="payrollReadNameAI">Identificar nome com IA</button>'+
        '<small id="payrollAIMessage" class="hint" role="status">A IA analisa o nome do recebedor na imagem do holerite mediante autorização.</small>'+
      '</div>'+
      '<p class="hint" id="payrollRecognitionHint" style="grid-column:1/-1"></p>'+
    '</form>',
    'Continuar para o holerite',async()=>{
      const form=$('payrollRecognitionForm');
      if(!form.reportValidity())return false;
      const employeeId=Number($('payrollRecognizedExisting').value);
      const enteredName=$('payrollRecognizedName').value.trim();
      const pix=$('payrollRecognizedPix').value.trim();
      let employee=payrollEmployees.find(e=>e.id===employeeId);
      if(!employee)employee=matchPayrollEmployee(enteredName);
      if(employee){
        if(!employee.chave_pix){
          if(!pix)throw new Error('Informe a chave Pix deste funcionário.');
          await api('/api/holerites/funcionarios','POST',{
            id:employee.id,nome:employee.nome,chave_pix:pix
          });
          const list=await api('/api/holerites');
          payrollEmployees=list.funcionarios||[];
          employee=payrollEmployees.find(e=>e.id===employee.id);
        }
        if(!employee)throw new Error('Não foi possível encontrar o funcionário.');
        accountModal(null,preview,null,{payroll:true,employee});
      }else{
        if(enteredName.length<3)throw new Error('Confirme o nome do recebedor.');
        if(!pix)throw new Error('Informe a chave Pix padrão.');
        // O cadastro ocorre atomicamente com o holerite no botão Salvar.
        accountModal(null,preview,null,{
          payroll:true,pendingEmployee:{nome:enteredName,chave_pix:pix}
        });
      }
      return true;
    });
  function updateRecognition(){
    const selected=payrollEmployees.find(e=>
      e.id===Number($('payrollRecognizedExisting').value));
    $('payrollRecognitionNameField').hidden=Boolean(selected);
    $('payrollRecognizedName').required=!selected;
    const needsPix=!selected||!String(selected.chave_pix||'').trim();
    $('payrollRecognitionPixField').hidden=!needsPix;
    $('payrollRecognizedPix').required=needsPix;
    $('payrollRecognitionHint').textContent=selected
      ?(selected.chave_pix
        ?'Funcionário existente. O Pix já cadastrado será reutilizado.'
        :'Funcionário existente sem Pix. Informe a chave uma única vez.')
      :'Novo recebedor: confirme o nome e cadastre o Pix. O funcionário será criado junto com este holerite.';
  }
  $('payrollRecognizedExisting').addEventListener('change',updateRecognition);
  $('payrollReadNameAI').addEventListener('click',async()=>{
    const status=$('payrollAIMessage'),button=$('payrollReadNameAI');
    if(!aiStatus.enabled||!aiStatus.consented){
      status.textContent='Primeiro conecte e autorize o ChatGPT no topo da tela. Depois clique novamente aqui.';
      return;
    }
    button.disabled=true;
    status.textContent='Conferindo visualmente o nome no holerite com IA...';
    try{
      const result=await api('/api/holerites/identificar','POST',{
        ticket:preview.ticket
      });
      if(!result.funcionario_nome){
        status.textContent='A IA não identificou um nome com segurança. Confira o PDF e corrija manualmente.';
        return;
      }
      const name=String(result.funcionario_nome).trim();
      $('payrollRecognizedName').value=name;
      const match=matchPayrollEmployee(name);
      $('payrollRecognizedExisting').value=match?String(match.id):'';
      updateRecognition();
      status.textContent='Nome identificado: '+name+'. Confira e informe a chave Pix se for novo.';
    }catch(error){
      status.textContent='Não foi possível usar a IA: '+(error.message||String(error));
    }finally{button.disabled=false;}
  });
  updateRecognition();
}
function payrollShortMonth(value){
  const match=String(value||'').match(/^(20\d{2})-(0[1-9]|1[0-2])$/);
  if(!match)return String(value||'—');
  const d=new Date(Number(match[1]),Number(match[2])-1,1);
  return d.toLocaleDateString('pt-BR',{month:'long',year:'numeric'});
}
function renderPayroll(){
  if(!$('payrollList'))return;
  const target=$('payrollList');
  const query=$('payrollSearch').value.trim().toLocaleLowerCase('pt-BR');
  const linked=accounts.filter(a=>a.holerite);
  const pending=accounts.filter(a=>a.holerite_sugerido&&!a.holerite);
  const people=payrollEmployees.filter(e=>{
    if(!query)return true;
    return String(e.nome||'').toLocaleLowerCase('pt-BR').includes(query)||
      linked.some(a=>a.funcionario_id===e.id&&[
        a.competencia,a.observacoes,a.fornecedor,a.status
      ].some(x=>String(x||'').toLocaleLowerCase('pt-BR').includes(query)));
  });
  const out=[];
  if(!people.length&&!pending.length){
    target.innerHTML='<div class="payroll-empty">'+(query?'Nenhum funcionário ou holerite corresponde à pesquisa.':
      'Nenhum holerite cadastrado. Importe um contracheque ou cadastre o primeiro funcionário.')+'</div>';
    return;
  }
  for(const e of people){
    const history=linked.filter(a=>a.funcionario_id===e.id).sort((a,b)=>
      String(b.competencia).localeCompare(String(a.competencia))||
      String(b.vencimento).localeCompare(String(a.vencimento))||
      b.id-a.id);
    const paid=history.filter(a=>a.status==='Paga').length;
    const months=history.map(a=>
      '<div class="payroll-month">'+
        '<strong>'+esc(payrollShortMonth(a.competencia))+'</strong>'+
        '<span title="'+esc(a.observacoes||'')+'">'+esc(a.observacoes||'Holerite mensal')+'</span>'+
        '<span class="amount">'+esc(money(a.valor))+'</span>'+
        '<span class="pill '+esc(a.status.replace(/\s/g,''))+'">'+esc(a.status)+'</span>'+
        '<button type="button" data-payroll-account="'+Number(a.id)+'">Ações</button>'+
      '</div>'
    ).join('');
    out.push(
      '<article class="payroll-person">'+
        '<div class="payroll-person-head">'+
          '<div class="payroll-person-details">'+
            '<span class="payroll-avatar">'+esc(initials(e.nome))+'</span>'+
            '<div><strong>'+esc(e.nome)+'</strong>'+
            '<small>'+history.length+' holerite(s) · '+paid+' pago(s) · Pix: '+
              esc(e.chave_pix||'não cadastrado')+'</small></div>'+
          '</div>'+
          '<div class="payroll-actions">'+
            (e.chave_pix?'<button type="button" data-payroll-copy="'+Number(e.id)+'">Copiar Pix</button>':'')+
            '<button type="button" data-payroll-edit="'+Number(e.id)+'">Editar Pix / Cadastro</button>'+
            '<button type="button" data-payroll-new="'+Number(e.id)+'">+ Holerite</button>'+
          '</div>'+
        '</div>'+
        (months||'<div class="payroll-empty">Ainda não existem holerites vinculados a este funcionário.</div>')+
      '</article>'
    );
  }
  const suggestions=pending.filter(a=>!query||[
    a.fornecedor,a.observacoes,a.vencimento
  ].some(x=>String(x||'').toLocaleLowerCase('pt-BR').includes(query)));
  if(suggestions.length){
    out.push('<section class="payroll-legacy"><h3>Holerites antigos para conferir</h3>'+
      '<p>Possíveis holerites encontrados pela descrição. Confirme o funcionário antes de vincular; nenhuma conta será alterada automaticamente.</p>'+
      suggestions.map(a=>'<div class="payroll-legacy-row">'+
        '<span>'+esc(a.fornecedor)+' · '+esc(dateBR(a.vencimento))+
        ' · '+esc(money(a.valor))+' · '+esc(a.observacoes||'')+'</span>'+
        '<button type="button" data-payroll-link="'+Number(a.id)+'">Vincular</button>'+
      '</div>').join('')+'</section>');
  }
  target.innerHTML=out.join('');
  for(const button of target.querySelectorAll('[data-payroll-copy]')){
    button.addEventListener('click',async()=>{
      const employee=payrollEmployees.find(e=>e.id===Number(button.dataset.payrollCopy));
      if(!employee?.chave_pix)return;
      try{await copyText(employee.chave_pix);notice('Chave Pix de '+employee.nome+' copiada.');}
      catch(e){notice(e.message||String(e),true);}
    });
  }
  for(const button of target.querySelectorAll('[data-payroll-edit]')){
    button.addEventListener('click',()=>{
      const employee=payrollEmployees.find(e=>e.id===Number(button.dataset.payrollEdit));
      if(employee)employeeModal(employee);
    });
  }
  for(const button of target.querySelectorAll('[data-payroll-new]')){
    button.addEventListener('click',()=>{
      const employee=payrollEmployees.find(e=>e.id===Number(button.dataset.payrollNew));
      if(employee)accountModal(null,null,null,{payroll:true,employee});
    });
  }
  for(const button of target.querySelectorAll('[data-payroll-account]')){
    button.addEventListener('click',()=>openMenu(Number(button.dataset.payrollAccount),{currentTarget:button}));
  }
  for(const button of target.querySelectorAll('[data-payroll-link]')){
    button.addEventListener('click',()=>{
      const a=accounts.find(a=>a.id===Number(button.dataset.payrollLink));
      if(a)accountModal(a);
    });
  }
}

function filteredAccountsForView(){
  const requested=$('status').value;
  const query=$('search').value.trim().toLocaleLowerCase('pt-BR');
  return accounts.filter(a=>{
    if(activeTab==='paid'&&a.status!=='Paga')return false;
    if(activeTab==='open'&&a.status==='Paga')return false;
    if(requested!=='all'&&a.status!==requested)return false;
    if(query&&![
      a.fornecedor,a.observacoes,a.documento,a.referencia,a.pagador,a.valor,
      a.vencimento,a.codigo_pagamento
    ].some(v=>String(v||'').toLocaleLowerCase('pt-BR').includes(query)))return false;
    return true;
  });
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
  const visible=filteredAccountsForView();
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
      +'<td data-label="Vencimento"><div class="due">'+esc(dateBR(a.vencimento))+'<small class="'+(a.status==='Vencida'?'late':'')+'">'+esc(a.status==='Paga'?'Pago':dueDescription)+'</small></div></td>'
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
  menu.querySelector('[data-action="copy-pdf-image"]').disabled=!a.has_pdf;
  menu.querySelector('[data-action="cancel-suggestion"]').disabled=!a.suggested_at;
  menu.querySelector('[data-action="copy-code"]').disabled=!String(a.codigo_pagamento||a.pix_padrao||'').trim();
  menu.querySelector('[data-action="send"]').disabled=a.status==='Paga'||!String(a.codigo_pagamento||a.pix_padrao||'').trim();
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
async function copyPDFAsImage(account){
  // O navegador autoriza escrita de PNG com interação direta do usuário.
  // O ClipboardItem recebe a Promise: a chamada de write acontece ainda
  // no clique, antes de qualquer await da rede (evita perda da ativacao).
  if(!navigator.clipboard?.write||typeof ClipboardItem==='undefined')
    throw new Error('Para copiar imagens, use Edge ou Chrome atualizado com HTTPS.');
  let loadError=null;
  const png=(async()=>{
    const url='/api/accounts/'+account.id+'/pdf-image';
    const load=()=>fetch(url,{headers:{'X-Local-Token':token}});
    let response=await load();
    if(response.status===403){
      const rejected=await response.clone().json().catch(()=>({}));
      if(/sess[aã]o inv[aá]lida|reabra pelo iniciador/i.test(String(rejected.error||''))){
        await renewSessionToken();
        response=await load();
      }
    }
    if(!response.ok){
      const problem=await response.json().catch(()=>({}));
      throw new Error(problem.error||'Não foi possível gerar a imagem do PDF.');
    }
    const blob=await response.blob();
    if(blob.type!=='image/png'||blob.size<20)
      throw new Error('O servidor não retornou uma imagem PNG válida.');
    return blob;
  })().catch(error=>{loadError=error;throw error;});
  try{
    await navigator.clipboard.write([new ClipboardItem({'image/png':png})]);
    notice('Imagem da primeira página do boleto copiada! Abra o WhatsApp e pressione Ctrl+V.',false,11000);
  }catch(error){
    if(loadError)throw loadError;
    throw new Error('O navegador não permitiu copiar a imagem. Verifique a permissão da área de transferência no Edge/Chrome.');
  }
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
async function exportExcel(){
  const visible=filteredAccountsForView();
  if(!visible.length){
    notice('Não há contas nesta lista para exportar.',true);return;
  }
  if(visible.length>6000){
    notice('A exportação aceita até 6.000 contas. Aplique um filtro para reduzir a lista.',true);return;
  }
  const button=$('exportExcel');
  button.disabled=true;
  const original=button.textContent;
  button.textContent='Gerando Excel...';
  try{
    const description=[
      activeTab==='paid'?'Contas pagas':'Contas em aberto',
      $('status').value!=='all'?'Status: '+$('status').value:null,
      $('search').value.trim()?'Pesquisa: '+$('search').value.trim():null
    ].filter(Boolean).join(' | ');
    const ids=visible.map(a=>a.id);
    const request=()=>fetch('/api/export/excel',{
      method:'POST',
      headers:{'X-Local-Token':token,'Content-Type':'application/json'},
      body:JSON.stringify({ids,label:description})
    });
    let response=await request();
    if(response.status===403){
      const detail=await response.clone().json().catch(()=>({}));
      if(/sess[aã]o inv[aá]lida|reabra pelo iniciador/i.test(String(detail.error||''))){
        await renewSessionToken();
        response=await request();
      }
    }
    if(!response.ok){
      const detail=await response.json().catch(()=>({}));
      throw new Error(detail.error||'Não foi possível gerar o Excel.');
    }
    const blob=await response.blob();
    if(blob.size<100||!blob.type.includes('spreadsheetml'))
      throw new Error('O servidor não retornou um arquivo Excel válido.');
    const url=URL.createObjectURL(blob);
    const anchor=document.createElement('a');
    anchor.href=url;
    anchor.download='CONTAS_'+(activeTab==='paid'?'PAGAS':'EM_ABERTO')+'_'+localToday()+'.xlsx';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    notice('Excel gerado com '+visible.length+' lançamento(s), respeitando os filtros da lista.',false,9000);
  }catch(error){
    notice(error.message||String(error),true,11000);
  }finally{
    button.disabled=false;
    button.textContent=original;
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
function accountModal(a,preview=null,rerun=null,opts={}){
  if(preview?.ticket)activePreviewTicket=preview.ticket;
  const analysis=preview||rerun;
  const suggested=analysis?.fields||{};
  const payrollHint=analysis?.holerite_sugestao||{};
  const asPayroll=Boolean(a?.holerite||a?.holerite_sugerido||
    opts.payroll||payrollHint.detected);
  const suggestedEmployee=String(opts.employee?.nome||opts.pendingEmployee?.nome||
    a?.funcionario_nome||payrollHint.funcionario_sugestao||'');
  const existingMatch=matchPayrollEmployee(suggestedEmployee);
  const employeeChosen=Number(opts.employee?.id||a?.funcionario_id||existingMatch?.id||0);
  const suggestedCompetence=String(a?.competencia||
    payrollHint.competencia_sugestao||localToday().slice(0,7));
  const originalFilename=preview&&typeof preview.original_filename==='string'
    ? preview.original_filename : '';
  const unsafe=(analysis?.insecure_fields||[]).map(v=>String(v));
  const form=accountFields.map(([key,title,type])=>{
    const wide=['fornecedor','codigo_pagamento','observacoes'].includes(key);
    const predictedValue=suggested[key];
    const readable=predictedValue!==null&&predictedValue!==undefined&&String(predictedValue).trim()!=='';
    const fallbackValue=a?(a[key]??''):
      key==='vencimento'?localToday():
      key==='fornecedor'&&asPayroll?suggestedEmployee:'';
    // Cadastro inicial: Observacoes inicia pelo nome do PDF original,
    // independentemente das sugestoes OCR. Em releitura de conta ja existente,
    // preservar as Observacoes cadastradas (sem mudar o nome do documento).
    const value=key==='fornecedor'&&asPayroll&&!a&&suggestedEmployee
      ?suggestedEmployee
      :key==='codigo_pagamento'&&asPayroll&&!a
      ?''
      :key==='observacoes'&&preview
      ? (originalFilename|| (readable?predictedValue:fallbackValue))
      : key==='observacoes'&&a ? fallbackValue
      : analysis&&readable?predictedValue:fallbackValue;
    const controls=type==='textarea'
      ?'<textarea name="'+key+'">'+esc(value)+'</textarea>'
      :'<input name="'+key+'" type="'+type+'" '+(type==='number'?'step="0.01" min="0.01"':'')+' value="'+esc(value)+'" '+(['fornecedor','valor','vencimento'].includes(key)?'required':'')+'>';
    return '<label class="field '+(wide?'wide':'')+'">'+title+controls+'</label>';
  }).join('');
  const employeeOptions=payrollEmployees.map(e=>
    '<option value="'+Number(e.id)+'"'+(employeeChosen===e.id?' selected':'')+'>'+
      esc(e.nome)+'</option>'
  ).join('');
  const payrollForm=
    '<section class="payroll-form-box">'+
      '<h4>Tratamento mensal do holerite</h4>'+
      '<label class="field wide">Tipo do lançamento<select name="holerite" id="isHolerite">'+
        '<option value="nao"'+(!asPayroll?' selected':'')+'>Conta normal</option>'+
        '<option value="sim"'+(asPayroll?' selected':'')+'>Holerite mensal</option>'+
      '</select></label>'+
      '<div id="payrollFields" style="display:contents"'+(!asPayroll?' hidden':'')+'>'+
        '<label class="field">Funcionário<select id="payrollEmployeePick" name="funcionario_id">'+
          '<option value="">Novo funcionário</option>'+employeeOptions+'</select></label>'+
        '<label class="field">Competência (mês) *'+
          '<input type="month" id="payrollCompetence" name="competencia" value="'+esc(suggestedCompetence)+'"></label>'+
        '<div id="payrollNewFields"'+(employeeChosen?' hidden':'')+'>'+
          '<label class="field">Nome do funcionário *'+
            '<input type="text" name="funcionario_nome" id="payrollEmployeeName" maxlength="150" value="'+esc(suggestedEmployee)+'"></label>'+
          '<label class="field">Pix padrão do funcionário *'+
            '<input type="text" name="pix_padrao" id="payrollDefaultPix" maxlength="150" value="'+esc(opts.pendingEmployee?.chave_pix||'')+'" placeholder="CPF, telefone, e-mail ou chave aleatória"></label>'+
        '</div>'+
        '<p class="payroll-info" id="payrollPixPreview"></p>'+
        '<p class="payroll-info">Cada holerite é uma conta independente. A chave Pix fica no cadastro permanente do funcionário; o valor é informado em cada mês.</p>'+
      '</div>'+
    '</section>';
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
    '<form id="accountForm" class="formgrid">'+form+payrollForm+'</form>',
    preview?'Confirmar e salvar PDF':a?'Salvar alterações':'Criar conta',async()=>{
      const form=$('accountForm');
      if(!form.reportValidity())return false;
      const values=Object.fromEntries(new FormData(form));
      if(values.holerite==='sim'){
        if(!values.competencia)throw new Error('Informe o mês de competência do holerite.');
        if(!values.funcionario_id&&!String(values.funcionario_nome||'').trim())
          throw new Error('Informe o funcionário do holerite.');
      }
      // Solicitar permissao no clique ANTES da requisicao ao servidor.
      if(preview?.ticket&&pendingImportPDF?.ticket===preview.ticket)
        await ensureAutomaticWindowsFolder();
      const route=preview?'/api/import/commit':a?'/api/accounts/'+a.id+'/edit':'/api/accounts';
      const saved=await api(route,'POST',preview?{...values,ticket:preview.ticket}:values);
      let localCopy=null;
      if(preview?.ticket&&pendingImportPDF?.ticket===preview.ticket){
        localCopy=await autoSaveFinalPdfToWindows(
          saved.id||saved.account_id,values
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
  const payrollType=$('isHolerite'), employeeSelect=$('payrollEmployeePick');
  function updateHoleriteControls(){
    const isPayroll=payrollType.value==='sim';
    const fields=$('payrollFields');
    fields.hidden=!isPayroll;
    fields.style.display=isPayroll?'contents':'none';
    const newFields=$('payrollNewFields');
    newFields.hidden=Boolean(employeeSelect.value);
    $('payrollEmployeeName').required=isPayroll&&!employeeSelect.value;
    $('payrollDefaultPix').required=isPayroll&&!employeeSelect.value;
    $('payrollCompetence').required=isPayroll;
    const existing=payrollEmployees.find(e=>e.id===Number(employeeSelect.value));
    if(isPayroll&&!a){
      const receiver=$('accountForm')?.querySelector('[name="fornecedor"]');
      const correctName=existing?.nome||$('payrollEmployeeName')?.value.trim();
      if(receiver&&correctName)receiver.value=correctName;
    }
    $('payrollPixPreview').textContent=existing
      ? 'Pix padrão recuperado: '+(existing.chave_pix||'não cadastrado — edite o cadastro do funcionário')
      : 'Novo funcionário: informe a chave Pix padrão uma única vez.';
  }
  payrollType.addEventListener('change',updateHoleriteControls);
  employeeSelect.addEventListener('change',updateHoleriteControls);
  $('payrollEmployeeName').addEventListener('input',()=>{
    if(payrollType.value==='sim'&&!employeeSelect.value&&!a){
      const receiver=$('accountForm')?.querySelector('[name="fornecedor"]');
      if(receiver)receiver.value=$('payrollEmployeeName').value.trim();
    }
  });
  updateHoleriteControls();
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
function receiptUploadMime(file){
  // Extensao do arquivo ajuda quando o Windows informa MIME vazio.
  const name=String(file?.name||'').toLowerCase();
  const mimeByExtension={
    pdf:'application/pdf',jpg:'image/jpeg',jpeg:'image/jpeg',
    png:'image/png',webp:'image/webp',bmp:'image/bmp',
    gif:'image/gif',tif:'image/tiff',tiff:'image/tiff'
  };
  const extension=name.split('.').pop();
  const mime=mimeByExtension[extension]||String(file?.type||'').toLowerCase();
  if(!['application/pdf','image/jpeg','image/png','image/webp',
        'image/bmp','image/gif','image/tiff'].includes(mime))
    throw new Error('Formato não aceito. Selecione PDF, JPG, PNG, WebP, BMP, GIF ou TIFF.');
  return mime;
}
function payModal(a){
  if(a.status==='Paga'){notice('Conta já paga.',true);return}
  const now=localToday();
  let checkingReceipt=false,receiptResult=null;
  showModal('Registrar pagamento',a.fornecedor+' · '+money(a.valor),
    '<label class="field">Data do pagamento<input type="date" id="paidDate" value="'+now+'"></label>'
    +'<label class="field" style="margin-top:15px">Comprovante PDF ou imagem (opcional)<input type="file" id="receiptFile" accept=".pdf,application/pdf,.jpg,.jpeg,image/jpeg,.png,image/png,.webp,image/webp,.bmp,image/bmp,.gif,image/gif,.tif,.tiff,image/tiff"></label>'
    +'<div class="receipt-check" id="receiptCheck" aria-live="polite"></div>'
    +'<p class="hint">JPG, PNG, WebP e outras imagens são convertidas em PDF automaticamente. O boleto e o comprovante ficam em um único PDF. Para imagens, confira o documento e confirme manualmente a associação. Sem comprovante: registro manual.</p>',
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
        await ensureAutomaticWindowsFolder();
        const proofMime=receiptUploadMime(proof);
        const response=await fetch('/api/accounts/'+a.id+'/pay-with-receipt',{
          method:'POST',headers:{'X-Local-Token':token,
            'Content-Type':proofMime,'X-Paid-Date':paidOn,
            'X-Receipt-Manual-Confirmed':checked?'yes':'no'},body:proof
        });
        const result=await response.json().catch(()=>({error:'Resposta de pagamento inválida.'}));
        if(!response.ok||!result.ok)throw new Error(result.error||'Pagamento não registrado.');
        const copy=await autoSaveFinalPdfToWindows(a.id,a);
        notice(copy?.saved?'Pagamento registrado. PDF final salvo no R2 e no Windows.':
          'Pagamento registrado. O PDF final foi salvo no R2.',false,9500);
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
    status.textContent='Preparando e conferindo o comprovante...';
    try{
      const proofMime=receiptUploadMime(proof);
      status.textContent=proofMime==='application/pdf'
        ?'Conferindo identificadores do comprovante...'
        :'Convertendo a imagem em PDF e preparando a conferência...';
      const response=await fetch('/api/accounts/'+a.id+'/verify-receipt',{
        method:'POST',headers:{'X-Local-Token':token,
          'Content-Type':proofMime},body:proof
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
        const candidates=answer.candidate_ids||[];
        detail.textContent=candidates.length>1
          ?'Contas semelhantes: '+candidates.join(', ')+'.'
          :'Conferência visual necessária antes de registrar este comprovante.';
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
      await ensureAutomaticWindowsFolder();
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
      const associated=eligible.find(a=>a.id===id);
      if(associated)await autoSaveFinalPdfToWindows(id,associated);
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
    else if(kind==='copy-pdf-image')await copyPDFAsImage(a);
    else if(kind==='attach')$('uploadFile').click();
    else if(kind==='reanalyze'){
      const read=await api('/api/accounts/'+a.id+'/analyze');
      accountModal(a,null,read);
    }
    else if(kind==='copy-code'){
      const code=String(a.codigo_pagamento||a.pix_padrao||'').trim();
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
  await ensureAutomaticWindowsFolder();
  const response=await postPDF('/api/accounts/'+selected+'/upload-pdf',file,{
    'X-Pdf-Name':encodeURIComponent(file.name)
  });
  const data=await response.json().catch(()=>({error:'Falha ao enviar PDF.'}));
  if(!response.ok||!data.ok)throw new Error(data.error||'Falha ao importar PDF.');
  const registered=accounts.find(item=>item.id===selected)||{};
  const savedLocal=await autoSaveFinalPdfToWindows(selected,registered);
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
    button.title=aiStatus.enabled
      ?'IA disponível para leitura de nome em holerite e correções autorizadas.'
      :'IA opcional. A leitura automática de holerite exige autorização própria.';
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
      '> Habilitar ChatGPT para as correções e identificações manuais</label>'+
      '<label><input id="aiAutoPayroll" type="checkbox" '+
      (aiStatus.auto_holerites?'checked':'')+
      '> Identificar automaticamente o nome do funcionário ao importar um holerite</label>'+
      '<p>Quando esta segunda opção estiver ligada, a primeira página do holerite poderá ser enviada à OpenAI para leitura visual do nome do recebedor, sem clique adicional. '+
      'Se desligada, a IA só será usada quando você solicitar manualmente. O resultado deve ser conferido antes de salvar. '+
      'Pode consumir a cota autorizada da conexão ChatGPT.</p>'+
      '<button type="button" id="disconnectAI" class="outline">Desconectar ChatGPT</button></div>',
      'Salvar configuração',async()=>{
        await api('/api/ai/consent','POST',{
          enabled:$('aiEnabled').checked,
          auto_holerites:$('aiEnabled').checked&&$('aiAutoPayroll').checked
        });
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
  if(!window.confirm('Conectar ChatGPT para leitura visual de documentos quando você autorizar? A identificação automática de holerites ficará DESATIVADA até ser habilitada nas configurações. Ao solicitar uma análise, a imagem do PDF será enviada à OpenAI.'))return;
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
$('exportExcel').addEventListener('click',exportExcel);
$('tabContas').addEventListener('click',()=>setModule('contas'));
$('tabHolerites').addEventListener('click',()=>setModule('holerites'));
$('employeeNew').addEventListener('click',()=>employeeModal());
$('payrollNew').addEventListener('click',()=>accountModal(null,null,null,{payroll:true}));
$('payrollSearch').addEventListener('input',renderPayroll);
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
    startDetectedHolerite(result);
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
      '<button type="button" id="testWindowsFolder" class="outline">Testar gravação</button>'+
      '<p class="hint">Selecione a pasta BOLETOS principal, nao uma subpasta de ano/mes. '+
      'O teste cria e remove um arquivo temporario, sem alterar seus boletos. '+
      'Se a autorizacao do navegador expirar, clique em Autorizar pasta salva. '+
      'O CONTAS 4 envia ao R2 e copia para o Windows automaticamente. '+
      'Pendencias sao repetidas assim que a pasta estiver acessivel, sem sincronizacao manual.</p>',
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
      const queue=$('windowsCopyQueueStatus');
      if(!text||!button||!queue)return;
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
        ?pending.length+' PDF(s) na fila de copia automatica do Windows.'
        :'Todas as copias recentes foram processadas neste navegador.';
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
        await retryWindowsCopiesAutomatically();
        notice('Pasta '+root.name+' autorizada. Copias pendentes processadas automaticamente.');
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
        await testWindowsFolderWrite(chosen);
        await updateFolderStatus();
        await retryWindowsCopiesAutomatically();
        notice('Pasta '+chosen.name+' autorizada. Cópia automática R2 e Windows ativada.');
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
  if(!e.target.closest('#menu')&&!e.target.closest('tbody tr')&&
     !e.target.closest('[data-payroll-account]'))closeMenu();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeMenu();closeModal();}});
$('cancel').addEventListener('click',()=>closeModal());
$('backdrop').addEventListener('click',e=>{if(e.target===$('backdrop'))closeModal()});
$('save').addEventListener('click',async()=>{
  if(!dialogSave)return;
  $('save').disabled=true;
  try{
    const handler=dialogSave;
    const ok=await handler();
    // Quando o reconhecimento de funcionário abre a conferência do holerite,
    // não fechar o formulário recém-aberto por engano.
    if(ok&&dialogSave===handler)closeModal({saved:true});
  }
  catch(e){notice(e.message||String(e),true)}
  finally{$('save').disabled=false}
});
reload().catch(e=>notice('Não foi possível carregar o banco: '+e.message,true));
setInterval(()=>{
  if(!$('backdrop').classList.contains('show')&&!$('menu').classList.contains('visible'))
    reload().catch(()=>{});
},15000);
