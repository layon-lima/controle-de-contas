# CONTROLE DE CONTAS — PREPARACAO PARA VPS — ETAPA 1

**Fonte oficial do desenvolvimento: branch main.** Esta etapa nao faz deploy.

## Contrato de funcionamento

- Endereco obrigatorio: https://contas-cloud.fazendanovohorizonte.com/
- VPS: mesma VPS de GFARM e PLUV; manter seus servicos, bancos, proxy e
  volumes absolutamente isolados.
- Todos os PDFs e comprovantes permanecem somente no PC Windows:
  C:\Users\User\Documents\BOLETOS\BOLETOS 2027\01 - JANEIRO\
- Reutilizar pastas existentes; criar somente as ausentes segundo o vencimento.
- Comprovante deve continuar sendo mesclado no PC ao PDF da conta, e somente
  apos confirmacao do usuario.
- PC desligado: app da VPS continua online; tarefas locais de PDF aguardam.
- NAO fazer upload, persistir, guardar ou cachear PDFs no disco da VPS.
- NAO trocar DNS, Caddy ou rota atual Cloudflare ate toda a migracao passar.

## Estado atual — nao expor o servidor local!

O servidor atual foi feito para localhost (token injetado no HTML,
SQLite local, caminhos absolutos de Windows, OCR legado dependente do Windows).
Nao e seguro publicar a porta 8767 na internet. O aplicativo completo
continua no Windows ate que a autenticacao remota, o agente local e a
migracao de dados estejam comprovadamente prontos.

## Verificacao isolada de codigo na VPS

    docker compose -f deploy/vps/compose.yml --profile preparacao run --rm contas-preflight

Este comando confere fontes de codigo SOMENTE LEITURA. Ele nao publica API,
nao abre porta, nao escreve banco e nao monta a pasta de PDFs do usuario.
Resultado esperado: ready_to_publish = false. Isso e proposital.

## Inventario no Windows

No PowerShell do computador original, estando na raiz do repositorio:

    python .\tools\CONTAS_INVENTARIAR_PDFS.py

O script le as tabelas contas e pagamentos com sqlite mode=ro, confere as
referencias dos PDFs e informa quantidade ausente/fora da pasta. Nao move,
nao altera nem envia documentos. Relatorio detalhado opcional:

    python .\tools\CONTAS_INVENTARIAR_PDFS.py --output "$env:LOCALAPPDATA\ControleContas\inventario_migracao.json"

O relatorio e privado, guarda caminhos locais e NAO deve ir para GitHub.

## Proximas etapas a programar na main

1. Backend VPS com login/autenticacao real, HTTPS e protecao do proxy;
   nunca publicar a API localhost sem autenticacao remota.
2. Agente Windows autenticado para ler, criar e mesclar PDFs no PC, com fila
   segura e pendencias quando PC estiver offline; sem copiar PDFs a VPS.
3. Banco principal migrado para VPS com identificadores logicos de documentos,
   nao caminhos C:\ no Linux. Backup e corte planejados, sem banco paralelo.
4. OCR/aprendizado original preservados ou executados pelo agente Windows.
5. Testes de contas, pagamentos, leitura OCR, comprovantes, contingencias,
   integridade de dados, recuperacao e PC desligado.
6. Cortar o endereco EXISTENTE somente apos aprovacao e testes completos.

Nenhum servidor Windows nem servico Cloudflare e alterado nesta etapa.
