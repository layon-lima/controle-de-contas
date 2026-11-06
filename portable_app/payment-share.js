'use strict';
(function(){
  const button=document.getElementById('copy-code');
  const output=document.getElementById('copy-status');
  const content=document.getElementById('payment-code');
  if(!button||!content||!output)return;
  button.addEventListener('click',async()=>{
    const code=content.textContent.trim();
    if(!code){output.textContent='Esta conta não possui um código de pagamento.';return;}
    try{
      if(navigator.clipboard?.writeText){
        await navigator.clipboard.writeText(code);
      }else{
        const temporary=document.createElement('textarea');
        temporary.value=code;
        temporary.style.position='fixed';
        temporary.style.opacity='0';
        document.body.appendChild(temporary);
        temporary.select();
        const success=document.execCommand('copy');
        temporary.remove();
        if(!success)throw new Error('Cópia bloqueada pelo navegador.');
      }
      output.textContent='Código copiado! Cole no aplicativo do seu banco.';
      button.textContent='Código copiado ✓';
    }catch(error){
      output.textContent='A cópia foi bloqueada. Pressione o código acima para selecioná-lo e copiar.';
    }
  });
})();