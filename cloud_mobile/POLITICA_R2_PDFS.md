# Politica R2 - PDFs somente de contas em aberto

**Regra estabelecida:** todos os PDFs originais continuam no Windows,
na pasta C:\Users\User\Documents\BOLETOS, organizada pelo ano/mes do
vencimento. A Cloudflare R2 tem copia temporaria do PDF e das imagens das
paginas SOMENTE enquanto a conta nao foi paga.

## Ciclo de vida

1. Cadastrou conta nao paga com PDF local: sincroniza PDF e imagens das
   paginas ao R2, permitindo que o celular visualize com o PC desligado.
2. Pagou: registrar o pagamento no banco ORIGINAL do Windows, concluir
   a mesclagem do comprovante localmente quando houver, e sincronizar.
3. O sincronizador envia status pago e um sinal se o PDF original existe,
   possui cabecalho PDF valido e esta sob Documents/BOLETOS.
4. O Worker oculta imediatamente o PDF pago no mobile e so permite
   a exclusao no R2 apos a confirmacao local. Exclui todos os objetos
   com prefixo accounts/ID/, inclusive imagens JPG e uploads incompletos.
5. Falha de rede/R2: mantem referencia de limpeza pendente e repete na
   proxima sincronizacao. Nunca apaga, renomeia ou move o PDF do Windows.
6. Pagamento revertido: o arquivo do PC permanece. Quando a conta
   retornar a nao paga, sera solicitado novo upload ao R2.

**Enviado para pagamento** e **sugerido para pagamento** nao sao
pagamentos confirmados: estes boletos continuam no R2.

## Sem PDF local seguro

Se o PDF de uma conta paga estiver faltando, corrompido, fora da pasta
oficial ou ilegivel, sua copia NO R2 NAO e removida, para evitar perda do
unico documento. A versao mobile exibe a conta como paga sem permitir
abrir o PDF. O log do sincronizador registra exclusoes pendentes.
Nao desbloquear a exclusao ate regularizar o arquivo original no PC.

## Custos e limites

R2 ainda tem armazenamento e operacoes sujeitos a limites. O programa
nao garante que o volume de contas abertas permanecera abaixo da faixa
gratuita: PDF e imagens de cada pagina ocupam espaco. A limpeza ocorre
na proxima sincronizacao online do PC, nao instantaneamente se o PC estiver
desligado.

## TESTES (sem credenciais ou banco real)

Na raiz do repositorio:
    python -m unittest discover -s tests -p test_r2_pdf_policy.py -v
    node --test cloud_mobile/tests/r2_paid_policy.test.mjs

E necessario Node.js 20+ para o teste do Worker.

## Publicacao - ORDEM OBRIGATORIA, APOS VALIDACAO

1. Testar a versao da main no Windows em AMBIENTE DE TESTE, usando dados
   e arquivos ficticios; nunca experimentar exclusao R2 em producao.
2. Aplicar primeiro o Worker atualizado, usando credenciais Cloudflare
   locais de administrador, sem gravar segredos no repositorio.
   A configuracao existente em cloud_mobile/wrangler.toml preserva o
   dominio contas-cloud.fazendanovohorizonte.com e bucket atual.
3. Atualizar o sincronizador realmente EXECUTADO no PC (um executavel
   compilado antigo nao passa a usar codigo novo so por git pull).
4. Conferir 1 conta aberta e 1 conta paga ficticias, mobile com PC
   desligado, exclusoes R2 e reversoes, e verificar copia Windows.
5. Manter backups independentes. Nunca usar a remocao do R2 como
   confirmacao financeira de pagamento.

**O commit na main nao faz deploy no Cloudflare nem atualiza o app Windows.**
