let accounts = [];
let activeFilter = "Todas";
let currentAccount = null;
let currentPdfImageUrl = null;
let pdfPage = 1;
let pdfPageCount = 0;
let pdfZoom = 1;

const el = (id) => document.getElementById(id);
const money = (value) =>
  new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL"
  }).format(Number(value || 0));

function normalize(value){
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g,"")
    .toLowerCase()
    .replace(/[^\p{L}\p{N},./-]+/gu," ")
    .trim();
}

function dateVariants(value){
  const text = String(value ?? "").trim();
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(!match) return [text];

  const [,year,month,day] = match;
  const names = [
    "janeiro","fevereiro","março","abril","maio","junho",
    "julho","agosto","setembro","outubro","novembro","dezembro"
  ];

  return [
    text,
    `${day}/${month}/${year}`,
    `${month}/${year}`,
    `${names[Number(month)-1]} ${year}`,
    names[Number(month)-1]
  ];
}

function searchableText(account){
  const values = [];

  for(const [key,value] of Object.entries(account)){
    if(value === null || value === undefined || value === "") continue;
    values.push(value);

    if(["vencimento","data_pagamento","criado_em","atualizado_em","enviado_pagamento_em"].includes(key)){
      values.push(...dateVariants(value));
    }

    if(["valor","valor_pago"].includes(key)){
      values.push(money(value));
      values.push(Number(value || 0).toFixed(2).replace(".",","));
    }
  }

  return normalize(values.join(" "));
}

function matchesSearch(account, query){
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  if(!terms.length) return true;
  const haystack = searchableText(account);
  return terms.every(term => haystack.includes(term));
}

function statusClass(status){
  return status === "Em dia" ? "Em-dia" : status;
}

function renderSummary(){
  const groups = {
    Vencida: accounts.filter(a=>a.status==="Vencida"),
    Próxima: accounts.filter(a=>a.status==="Próxima"),
    "Em dia": accounts.filter(a=>a.status==="Em dia"),
  };

  const sum = arr => arr.reduce((acc,a)=>acc+Number(a.valor || 0),0);
  const open = accounts.filter(a=>!String(a.data_pagamento || "").trim() && a.status!=="Paga");

  el("openTotal").textContent = money(sum(open));
  el("openCount").textContent =
    `${open.length} ${open.length===1 ? "conta pendente" : "contas pendentes"}`;

  el("countOverdue").textContent = groups.Vencida.length;
  el("valueOverdue").textContent = money(sum(groups.Vencida));

  el("countSoon").textContent = groups.Próxima.length;
  el("valueSoon").textContent = money(sum(groups.Próxima));

  el("countOk").textContent = groups["Em dia"].length;
  el("valueOk").textContent = money(sum(groups["Em dia"]));

}

function filteredAccounts(){
  const query = el("searchInput").value;
  return accounts.filter(a => {
    if (String(a.data_pagamento || "").trim() || a.status === "Paga") return false;
    const statusOk = activeFilter === "Todas" || a.status === activeFilter;
    return statusOk && matchesSearch(a,query);
  });
}

function displayDate(value){
  const text = String(value ?? "").trim();
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : (text || "—");
}

function activityDateTime(value){
  const ms = Date.parse(String(value || ""));
  if(!Number.isFinite(ms)) return "—";
  return new Intl.DateTimeFormat("pt-BR",{
    day:"2-digit",month:"2-digit",year:"numeric",
    hour:"2-digit",minute:"2-digit"
  }).format(new Date(ms));
}

function remainingCopyMinutes(account){
  const until = Date.parse(String(account?.codigo_copiado_ate || ""));
  if(!Number.isFinite(until)) return 0;
  return Math.max(0, Math.ceil((until - Date.now()) / 60000));
}

function renderPaymentActivity(account){
  const box = el("paymentActivity");
  if(!box) return;

  const pieces = [];
  if(account?.sugerido_em){
    pieces.push(`
      <div class="activity-line">
        <span class="activity-dot suggested"></span>
        <div><strong>Sugerido para pagamento</strong><small>${escapeHtml(activityDateTime(account.sugerido_em))}</small></div>
      </div>
    `);
  }

  if(account?.enviado_pagamento_em){
    pieces.push(`
      <div class="activity-line">
        <span class="activity-dot sent"></span>
        <div><strong>Enviado para pagamento</strong><small>${escapeHtml(activityDateTime(account.enviado_pagamento_em))}</small></div>
      </div>
    `);
  }

  const minutes = remainingCopyMinutes(account);
  if(minutes > 0){
    pieces.push(`
      <div class="copy-payment-tag">
        <strong>Copiado para pagamento</strong>
        <span>expira em ${minutes} min</span>
      </div>
    `);
  }

  box.innerHTML = pieces.join("");
  box.classList.toggle("hidden", pieces.length === 0);
}

function refreshTemporaryCopyFlags(){
  let changed = false;
  for(const account of accounts){
    if(account.codigo_copiado_recente && remainingCopyMinutes(account) <= 0){
      account.codigo_copiado_recente = false;
      account.codigo_copiado_ate = null;
      changed = true;
    }
  }
  if(currentAccount && currentAccount.codigo_copiado_recente && remainingCopyMinutes(currentAccount) <= 0){
    currentAccount.codigo_copiado_recente = false;
    currentAccount.codigo_copiado_ate = null;
    changed = true;
  }
  if(changed) renderList();
  if(currentAccount) renderPaymentActivity(currentAccount);
}

function renderList(){
  const items = filteredAccounts();
  const list = el("accountsList");
  list.innerHTML = "";

  el("resultCount").textContent = items.length;
  el("emptyState").classList.toggle("hidden",items.length>0);

  const titles = {
    Todas:"Contas em aberto",
    Vencida:"Contas vencidas",
    Próxima:"Próximas do vencimento",
    "Em dia":"Contas em dia"
  };
  el("listTitle").textContent = titles[activeFilter];

  for(const account of items){
    const card = document.createElement("button");
    card.className = "account-card";
    if(account.sugestao_pagamento) card.classList.add("payment-suggestion");

    const ref = account.referencia || "Sem referência";
    const obs = account.observacoes || "";
    const receiver = account.recebedor || "Sem recebedor";

    card.innerHTML = `
      <div class="account-top">
        <div>
          <div class="account-name">${escapeHtml(receiver)}</div>
          <div style="margin-top:7px">
            <span class="status ${statusClass(account.status)}">
              ${escapeHtml(account.status)}
            </span>
          </div>
        </div>
        <div class="account-right">
          ${account.sugestao_pagamento ? `<span class="suggestion-flag">SUGERIDO</span>` : ""}
          ${account.enviado_pagamento_em ? `<span class="sent-flag">ENVIADO PARA PAGAMENTO</span>` : ""}
          ${account.codigo_copiado_recente ? `<span class="copied-flag">COPIADO PARA PAGAMENTO</span>` : ""}
          <div class="account-value">${money(account.valor)}</div>
        </div>
      </div>
      <div class="account-meta">
        <div class="account-meta-main">
          <div class="meta-block meta-due">
            <span class="meta-label">Vencimento</span>
            <span class="meta-value">${escapeHtml(displayDate(account.vencimento))}</span>
          </div>
          <div class="meta-block meta-reference">
            <span class="meta-label">Referência</span>
            <span class="meta-value" title="${escapeHtml(ref)}">${escapeHtml(ref)}</span>
          </div>
        </div>
        ${obs ? `
          <div class="account-observation">
            <span class="meta-label">Observação</span>
            <span class="meta-value">${escapeHtml(obs)}</span>
          </div>
        ` : ""}
      </div>
    `;

    card.addEventListener("click",()=>openDetail(account));
    list.appendChild(card);
  }
}

function escapeHtml(value){
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[c]));
}

function setFilter(filter){
  activeFilter = filter;
  document.querySelectorAll(".chip").forEach(btn=>{
    btn.classList.toggle("active",btn.dataset.filter===filter);
  });
  renderList();
}

function openDetail(account){
  currentAccount = account;

  const badge = el("detailStatus");
  badge.textContent = account.status;
  badge.className = `status-badge status ${statusClass(account.status)}`;

  el("detailReceiver").textContent = account.recebedor || "Sem recebedor";
  el("detailObservation").textContent = account.observacoes || "Sem observação";
  el("detailValue").textContent = money(account.valor);
  el("detailDue").textContent = displayDate(account.vencimento);
  el("detailRef").textContent = account.referencia || "—";
  el("detailPayer").textContent = account.pagador || "—";
  el("detailType").textContent = account.tipo_pagamento || "—";
  el("paymentTypeLabel").textContent = account.tipo_pagamento || "Código";
  el("detailPaymentCode").textContent =
    account.codigo_pagamento || "Código não informado";

  const paid = account.status === "Paga";
  const hasCode = Boolean(String(account.codigo_pagamento || "").trim());

  el("paymentBox").classList.toggle("hidden",paid);
  el("paidMessage").classList.toggle("hidden",!paid);

  el("copyPayment").disabled = !hasCode;
  el("copyPayment").textContent =
    hasCode ? "Copiar código" : "Sem código";

  el("viewPdf").classList.toggle("hidden", !account.tem_pdf);
  renderPaymentActivity(account);

  el("scrim").classList.remove("hidden");
  el("detailSheet").classList.add("open");
  el("detailSheet").setAttribute("aria-hidden","false");
  document.body.style.overflow="hidden";
}

function closeDetail(){
  el("detailSheet").classList.remove("open");
  el("detailSheet").setAttribute("aria-hidden","true");
  el("scrim").classList.add("hidden");
  document.body.style.overflow="";
  currentAccount=null;
}

async function copyPayment(){
  const code = String(currentAccount?.codigo_pagamento || "").trim();
  if(!code) return;

  try{
    await navigator.clipboard.writeText(code);
  }catch{
    const ta = document.createElement("textarea");
    ta.value=code;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }

  const feedback = el("copyFeedback");
  feedback.classList.add("show");
  setTimeout(()=>feedback.classList.remove("show"),1400);

  // Registra apenas o ato de copiar. Isso NÃO marca a conta como paga.
  // No mobile a tag fica 15 minutos; o desktop mantém o histórico da cópia.
  try{
    const response = await fetch(`/api/mobile/copied/${encodeURIComponent(currentAccount.id)}?t=${Date.now()}`, {
      method:"POST",
      cache:"no-store",
      keepalive:true,
      headers:viewHeaders()
    });
    if(response.ok){
      const payload = await response.json().catch(()=>({}));
      const copiedAt = payload.copied_at || new Date().toISOString();
      const copiedUntil = payload.mobile_copied_until ||
        new Date(Date.now() + 15 * 60 * 1000).toISOString();

      currentAccount.codigo_copiado_recente = true;
      currentAccount.codigo_copiado_em = copiedAt;
      currentAccount.codigo_copiado_ate = copiedUntil;

      const stored = accounts.find(a=>Number(a.id)===Number(currentAccount.id));
      if(stored){
        stored.codigo_copiado_recente = true;
        stored.codigo_copiado_em = copiedAt;
        stored.codigo_copiado_ate = copiedUntil;
      }

      renderPaymentActivity(currentAccount);
      renderList();
    }
  }catch{}
}

function showLoadError(message){
  el("accountsList").innerHTML = `
    <div style="
      padding:18px;
      border:1px solid #efcaca;
      background:#fff3f3;
      color:#8b2929;
      border-radius:18px;
      line-height:1.5;
      font-size:13px;">
      <strong>Não consegui carregar as contas.</strong><br>
      ${escapeHtml(message)}
    </div>
  `;
  el("resultCount").textContent = "!";
}


const VIEW_TOKEN_KEY = "controle_contas_view_token";
const DEVICE_TOKEN_KEY = "controle_contas_device_token";

function getViewToken(){
  const fromHash = decodeURIComponent(location.hash.replace(/^#/, "").trim());
  if(fromHash){
    localStorage.setItem(VIEW_TOKEN_KEY, fromHash);
    return fromHash;
  }
  return localStorage.getItem(VIEW_TOKEN_KEY) || "";
}

function getDeviceToken(){
  const params = new URLSearchParams(location.search);
  const fromUrl = String(params.get("device") || "").trim();
  if(fromUrl){
    localStorage.setItem(DEVICE_TOKEN_KEY, fromUrl);
    return fromUrl;
  }
  return localStorage.getItem(DEVICE_TOKEN_KEY) || "";
}

function updatePersistentManifest(deviceToken){
  if(!deviceToken) return;
  let manifest = document.querySelector('link[rel="manifest"]');
  if(!manifest){
    manifest = document.createElement("link");
    manifest.rel = "manifest";
    document.head.appendChild(manifest);
  }
  manifest.href = "/api/manifest?device=" + encodeURIComponent(deviceToken);
}

function keepPersistentStartUrl(deviceToken){
  if(!deviceToken) return;
  const url = new URL(location.href);
  url.hash = "";
  if(!url.searchParams.get("device")){
    url.searchParams.set("device", deviceToken);
    history.replaceState(null, "", url.pathname + url.search);
  }
}

function viewHeaders(){
  const deviceToken = getDeviceToken();
  if(deviceToken) return {"X-Device-Token":deviceToken};

  const viewToken = getViewToken();
  return viewToken ? {"X-View-Token":viewToken} : {};
}

async function ensureViewSession(){
  const deviceToken = getDeviceToken();
  const viewToken = getViewToken();
  if(!deviceToken && !viewToken) return false;

  const headers = deviceToken
    ? {"X-Device-Token":deviceToken}
    : {"X-View-Token":viewToken};

  try{
    const response = await fetch("/api/session?t=" + Date.now(), {
      method:"POST",
      cache:"no-store",
      headers
    });
    if(!response.ok) return false;

    const payload = await response.json().catch(()=>({}));
    const persistentToken = String(payload.device_token || deviceToken || "").trim();

    if(persistentToken){
      localStorage.setItem(DEVICE_TOKEN_KEY, persistentToken);
      updatePersistentManifest(persistentToken);
      keepPersistentStartUrl(persistentToken);

      try{
        if(navigator.storage && navigator.storage.persist){
          navigator.storage.persist().catch(()=>{});
        }
      }catch{}
    }
    return true;
  }catch{
    return false;
  }
}

async function loadAccounts(){
  if(el("syncText")) el("syncText").textContent = "Carregando dados...";

  if(getDeviceToken() || getViewToken()){
    await ensureViewSession();
  }

  try{
    const response = await fetch("/api/contas?t=" + Date.now(), {
      cache:"no-store",
      headers:viewHeaders()
    });
    const payload = await response.json();

    if(response.status === 401){
      throw new Error("A autorizacao deste aparelho precisa ser restaurada uma unica vez pelo link privativo.");
    }

    if(!response.ok || !payload.ok){
      throw new Error(payload.erro || "Falha ao ler as contas.");
    }

    // Compatibilidade com cache/Worker antigo: jamais mostrar contas pagas.
    accounts = (Array.isArray(payload.contas) ? payload.contas : [])
      .filter(a => a && !String(a.data_pagamento || "").trim() && a.status !== "Paga");

    const sync = payload.meta?.ultima_atualizacao;
    if(sync){
      const d = new Date(sync);
      if(el("syncText")) el("syncText").textContent =
        `Banco atualizado ${d.toLocaleDateString("pt-BR")} ${d.toLocaleTimeString("pt-BR",{hour:"2-digit",minute:"2-digit"})}`;
    }else{
      if(el("syncText")) el("syncText").textContent = "Dados carregados";
    }

    renderSummary();
    renderList();
  }catch(error){
    accounts = [];
    renderSummary();
    showLoadError(error.message || String(error));
    if(el("syncText")) el("syncText").textContent = "Falha ao carregar";
  }
}

document.querySelectorAll(".chip").forEach(btn=>{
  btn.addEventListener("click",()=>setFilter(btn.dataset.filter));
});

document.querySelectorAll(".stat-card").forEach(btn=>{
  btn.addEventListener("click",()=>setFilter(btn.dataset.filter));
});

el("searchInput").addEventListener("input",(e)=>{
  e.currentTarget.closest(".search-box")
    .classList.toggle("has-text",!!e.currentTarget.value);
  renderList();
});

el("clearSearch").addEventListener("click",()=>{
  el("searchInput").value="";
  el("searchInput").dispatchEvent(new Event("input"));
  el("searchInput").focus();
});

el("closeSheet").addEventListener("click",closeDetail);
el("scrim").addEventListener("click",closeDetail);
el("copyPayment").addEventListener("click",copyPayment);
setInterval(refreshTemporaryCopyFlags, 30000);

function revokePdfImageUrl(){
  if(currentPdfImageUrl){
    URL.revokeObjectURL(currentPdfImageUrl);
    currentPdfImageUrl = null;
  }
}

function setPdfZoom(value){
  pdfZoom = Math.max(0.65, Math.min(2.25, Number(value) || 1));
  const image = el("pdfPageImage");
  image.style.width = `${Math.round(pdfZoom * 100)}%`;
  el("pdfZoomLabel").textContent = `${Math.round(pdfZoom * 100)}%`;
}

function updatePdfNavigation(){
  el("pdfPageCurrent").textContent = String(pdfPage || 1);
  el("pdfPageTotal").textContent = String(pdfPageCount || 1);
  el("pdfPageBadge").textContent =
    pdfPageCount > 0 ? `${pdfPage} / ${pdfPageCount}` : "—";
  el("pdfPrev").disabled = pdfPage <= 1;
  el("pdfNext").disabled = pdfPageCount < 1 || pdfPage >= pdfPageCount;
}

function showPdfLoading(message = "Carregando a página..."){
  el("pdfLoading").classList.remove("hidden");
  const small = el("pdfLoading").querySelector("small");
  if(small) small.textContent = message;
}

function hidePdfLoading(){
  el("pdfLoading").classList.add("hidden");
}

async function loadPdfPage(pageNumber){
  if(!currentAccount?.id || pdfPageCount < 1) return;

  const page = Math.max(1, Math.min(pdfPageCount, Number(pageNumber) || 1));
  pdfPage = page;
  updatePdfNavigation();
  showPdfLoading("Renderizando a página...");
  el("pdfPageImage").classList.add("hidden");
  revokePdfImageUrl();

  try{
    const response = await fetch(
      `/api/pdf/${encodeURIComponent(currentAccount.id)}/page/${page}?t=${Date.now()}`,
      {
        cache:"no-store",
        headers:viewHeaders()
      }
    );

    if(!response.ok){
      let message = "Não foi possível carregar esta página.";
      try{
        const payload = await response.json();
        message = payload.erro || message;
      }catch{}
      throw new Error(message);
    }

    const blob = await response.blob();
    currentPdfImageUrl = URL.createObjectURL(blob);

    const image = el("pdfPageImage");
    image.onload = ()=>{
      hidePdfLoading();
      image.classList.remove("hidden");
    };
    image.onerror = ()=>{
      showPdfLoading("Não foi possível exibir esta página.");
    };
    image.src = currentPdfImageUrl;
  }catch(error){
    showPdfLoading(error.message || "Erro ao carregar a página.");
  }
}

async function openPdfViewer(){
  if(!currentAccount?.id || !currentAccount?.tem_pdf) return;

  el("pdfViewerTitle").textContent =
    currentAccount.recebedor || "Documento da conta";

  el("pdfScrim").classList.remove("hidden");
  el("pdfViewer").classList.remove("hidden");
  el("pdfViewer").setAttribute("aria-hidden","false");

  pdfPage = 1;
  pdfPageCount = 0;
  setPdfZoom(1);
  updatePdfNavigation();
  showPdfLoading("Preparando o documento...");
  el("pdfPageImage").classList.add("hidden");
  revokePdfImageUrl();

  try{
    const response = await fetch(
      `/api/pdf/${encodeURIComponent(currentAccount.id)}/meta?t=${Date.now()}`,
      {
        cache:"no-store",
        headers:viewHeaders()
      }
    );

    const payload = await response.json();

    if(!response.ok || !payload.ok){
      throw new Error(payload.erro || "Não foi possível preparar o PDF.");
    }

    pdfPageCount = Number(payload.paginas || 0);
    if(pdfPageCount < 1){
      throw new Error("Este PDF não possui páginas disponíveis.");
    }

    updatePdfNavigation();
    await loadPdfPage(1);
  }catch(error){
    showPdfLoading(error.message || "Erro ao abrir o documento.");
  }
}

function closePdfViewer(){
  el("pdfViewer").classList.add("hidden");
  el("pdfScrim").classList.add("hidden");
  el("pdfViewer").setAttribute("aria-hidden","true");
  el("pdfPageImage").classList.add("hidden");
  el("pdfPageImage").removeAttribute("src");
  showPdfLoading("Carregando a página...");
  revokePdfImageUrl();
  pdfPage = 1;
  pdfPageCount = 0;
  setPdfZoom(1);
  updatePdfNavigation();
}

el("viewPdf").addEventListener("click",openPdfViewer);
el("closePdf").addEventListener("click",closePdfViewer);
el("pdfScrim").addEventListener("click",closePdfViewer);
el("pdfPrev").addEventListener("click",()=>loadPdfPage(pdfPage - 1));
el("pdfNext").addEventListener("click",()=>loadPdfPage(pdfPage + 1));
el("pdfZoomOut").addEventListener("click",()=>setPdfZoom(pdfZoom - 0.15));
el("pdfZoomIn").addEventListener("click",()=>setPdfZoom(pdfZoom + 0.15));
el("pdfFit").addEventListener("click",()=>setPdfZoom(1));


const rememberedDeviceToken = getDeviceToken();
if(rememberedDeviceToken) updatePersistentManifest(rememberedDeviceToken);

loadAccounts();

// Enquanto esta versão estiver rodando no PC, atualiza a visualização
// periodicamente sem precisar recarregar a página.
setInterval(loadAccounts, 30000);



if("serviceWorker" in navigator){
  window.addEventListener("load",()=>{
    navigator.serviceWorker.register("/sw.js?v=13").catch(()=>{});
  });
}
