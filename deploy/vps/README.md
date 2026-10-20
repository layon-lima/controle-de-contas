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


## ETAPA 02 — Broker de metadados + agente Windows (codigo implementado)

Esta etapa adiciona um broker e o agente. NENHUM deles esta ligado ao
dominio existente, e nem o GFARM nem o PLUV devem ser reiniciados.

**Fluxo seguro para teste:** administrador cria pedido pdf_status no broker
(sem caminhos Windows), Windows consulta a fila por HTTPS usando token de agente,
Windows verifica seu SQLite local e retorna apenas:
available, within_official_root e size_bytes. Nenhum arquivo, caminho absoluto,
OCR ou nome de fornecedor e transmitido para a VPS. Jobs permanecem pendentes
se o Windows estiver desligado. Uma lease de 120s com retry evita perder jobs.

Os endpoints administrativos /internal/jobs estao reservados a chamadas
locais autorizadas. Jamais publicar essa rota no proxy reverso da internet.
A unica rota de agente a expor futuramente, apos configurar o Caddy e testar
seguranca, e /agent/jobs/next e /agent/jobs/{id}/result, ambas com token e TLS.
Nao criar regra Caddy, DNS ou tunel ainda.

### Testes sem dados reais

    python -m unittest tests.test_vps_broker_agent -v

Os testes montam SQLite e PDF ficticios em pasta temporaria, criam um broker
localhost, testam autenticação, duplicidade, indisponibilidade, selecao por ID
e verificam que NENHUM PDF e enviado pela rede.

### Implantacao somente depois de validar credenciais/rotas na VPS

Arquivo: deploy/vps/compose.broker.yml.
Crie deploy/vps/broker.env no servidor, com tokens aleatorios unicos gerados
em canal seguro, protegidos com chmod 600. Nao comite nem compartilhe tokens.
A implantacao futura usa:

    docker compose -f deploy/vps/compose.broker.yml up -d --build

A porta 18767 aceita conexoes apenas de 127.0.0.1 NA VPS. O volume
broker_metadata guarda SOMENTE fila e resultados pequenos de metadados.

No Windows, o arquivo privado (nao versionado)
%LOCALAPPDATA%\ControleContas\agent_vps.json tera, quando ativado:

    {
      "broker_url": "https://contas-cloud.fazendanovohorizonte.com/__agente",
      "agent_token": "CHAVE_PRIVADA_GERADA_NO_SERVIDOR",
      "documents_root": "C:\\Users\\User\\Documents\\BOLETOS",
      "database": "C:\\Users\\User\\AppData\\Local\\ControleContas\\data\\controle_contas.db"
    }

**Atencao:** esse exemplo indica o endereco planejado, NAO uma rota existente.
Nao executar agente ate configurarmos o proxy de modo restrito. O token do agente
deve ficar acessivel apenas ao usuario Windows. O agente nao recebe PDF,
nao realiza mesclagens nem alteracoes nesta etapa. Isso sera desenvolvido
apenas apos testar autorizacoes e idempotencia da fila.

A aplicacao antiga continua rodando no PC. O broker NAO e substituto de login,
banco principal ou OCR na VPS. A migracao final depende das proximas etapas.


## ETAPA 03 — Broker isolado e teste criptografado PC / VPS

Estamos usando a mesma VPS Ubuntu de GFARM e PLUV. Nao alterar os
servicos existentes. Nesta etapa NAO mudar o dominio atual
https://contas-cloud.fazendanovohorizonte.com/ nem o Caddy/Cloudflare.

O broker foi preparado para uma porta de loopback EXCLUSIVA na VPS:
127.0.0.1:18767, contêiner contas-agent-broker. Os tokens sao armazenados
somente em deploy/vps/broker.env (fora do Git, chmod 600).
O broker grava apenas IDs/estado de jobs e resultados pequenos. Nao guarda PDF.

Na VPS, apos clonar a branch main do repo privado para /opt/apps/contas:

    cd /opt/apps/contas
    bash deploy/vps/INSTALAR_BROKER.sh

O script e repetivel. Ele nao atualiza/reinicia GFARM, PLUV ou Caddy,
nao abre portas externas e NAO toca no dominio existente.

No PC Windows original, estando na main atualizada:

    .\tools\CONFIGURAR_AGENTE_VPS.ps1 -SshTarget root@187.127.63.29

Troque root se o usuario SSH da VPS for diferente. O SSH pede autenticacao
normalmente; nao compartilhe senha nem chaves na conversa. O script obtem
APENAS a chave do agente por SSH, sem exibi-la, e salva no PC com ACL privada.
Preserva C:\Users\User\Documents\BOLETOS e o SQLite original.

Para validar SEM expor o broker na internet, em uma segunda janela do PC:

    ssh -N -L 127.0.0.1:18767:127.0.0.1:18767 root@187.127.63.29

Deixe o tunel aberto. A conexao PC-VPS e cifrada por SSH; HTTP interno
funciona somente em localhost dos dois computadores.

Na VPS, crie um pedido (escolha uma conta existente, por exemplo 1):

    cd /opt/apps/contas
    python3 deploy/vps/PEDIDO_TESTE_BROKER.py --conta 1

No PC Windows, processe uma solicitacao:

    python .\tools\CONTAS_AGENTE_VPS.py --once

Na VPS, consulte o identificador retornado pela criacao:

    python3 deploy/vps/PEDIDO_TESTE_BROKER.py --consultar ID_DO_JOB

O resultado deve informar status done e metadados (available,
within_official_root, size_bytes). Nenhuma parte do PDF atravessa a rede.
O agente ainda nao inicia automaticamente; isso sera ativado apos os testes.

**Aviso:** esse tunel SSH e somente a verificacao segura inicial. O acesso
publico HTTPS por contas-cloud sera configurado depois, junto com autenticacao
da aplicacao web. NUNCA publique /internal/jobs ou a porta 18767 na internet.
