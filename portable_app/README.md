# Controle de Contas — versão portátil (experimental)

Depois que os arquivos da branch `develop` estiverem sincronizados na pasta
`CONTROLE_CONTAS_GITHUB`, abra com **dois cliques** em
`ABRIR_CONTROLE_DE_CONTAS.vbs`, na raiz do projeto. Esse iniciador não
instala nada, não mostra um prompt de comando e tenta atualizar o código
automaticamente a cada abertura. Se já houver um servidor portátil aberto,
ele reinicia somente esse servidor para carregar a versão nova.

A opção `START_CONTROLE_DE_CONTAS_PORTATIL.bat` fica como iniciador de diagnóstico.
Não é necessário executar PyInstaller, Inno Setup, npm ou instalar outro banco.
O Python 3.13 já usado no Controle de Contas é suficiente para iniciar o servidor.
A interface abre no navegador em `http://127.0.0.1:8767`.

**Dados preservados:** conecta ao mesmo arquivo SQLite do desktop,
`%LOCALAPPDATA%\ControleContas\data\controle_contas.db`. Não move nem recria
esse arquivo. Antes da primeira alteração em cada sessão cria uma cópia online
SQLite em `%LOCALAPPDATA%\ControleContas\backups\antes_portatil_*.db`.
Arquivos PDF previamente cadastrados são abertos do endereço original.
**Novos PDFs anexados pelo portátil** seguem a mesma `pasta_base` definida no
desktop clássico em `config_documentos.json`, sob
`BOLETOS [ANO]/[MM] - [MÊS]`, conforme o vencimento. O nome do arquivo deriva
de Observações (ou Fornecedor), como na versão instalada; nomes duplicados não
sobrescrevem PDFs anteriores. Se não houver pasta-base configurada, o arquivo
é salvo provisoriamente em `%LOCALAPPDATA%\ControleContas\documents\portatil`
e o app informa essa condição. Por ser um upload do navegador, o original que
estava na pasta de origem do usuário não é apagado. A edição manual dos dados
de uma conta não reorganiza automaticamente os arquivos já existentes.

**Interface:** tabela sem coluna de ações, card **Pagas** com histórico de
todos os períodos, menu por duplo clique, filtros por situação/TAG/pesquisa,
cadastro/edição, TAGs, sugestão Cloudflare, cópia com confirmação de envio,
pagamento manual, exclusão e leitura/anexo/visualização de PDFs.

**Importação com autopreenchimento:** o botão **Importar PDF** abre o motor OCR
original recuperado do desktop (incluindo perfis de aprendizado V1/V2,
regras DARF/FGTS e análise de PDF escaneado/híbrido), em processo Python isolado.
O motor é carregado de `.local_build_*` ou extraído localmente dos arquivos
`releases/CONTROLE_CONTAS_1.4.4_SOURCE.zip` e
`releases/CONTROLE_CONTAS_1.4.5_OVERLAY.b64`; não precisa reinstalar EXE.
A prévia não grava contas. O usuário pode corrigir todos os campos antes de
confirmar. Após salvar, o aprendizado V2 recebe o PDF e os valores confirmados.
Se o motor original falhar, o sistema mostra o erro em vez de substituir por
um OCR inferior silenciosamente.

**Comprovante:** ao registrar um pagamento, o comprovante PDF é opcional.
Se fornecido, a função mescla suas páginas às do boleto existente, valida a
quantidade de páginas, usa cópia de recuperação e registra o pagamento somente
depois da mesclagem. Se não houver boleto, o comprovante vira o PDF da conta.
Se a gravação do banco falhar, o arquivo original é restaurado. A conferência
automática do comprovante permanece desativada, assim como na última versão
instalada. O comprovante físico selecionado não é apagado do computador.

**Organização:** o botão **Pasta boletos** permite definir a mesma pasta-base
utilizada pelo desktop instalado. Novos PDFs são salvos em
`BOLETOS [ANO]/[MM] - [MÊS]`. PDFs antigos continuam preservados e links do
banco permanecem válidos. Um arquivo importado pelo navegador não é removido
de sua pasta de origem. Duplicatas comprovadas por bytes ou linha digitável
são bloqueadas, mas uma chave PIX estática ou dados semelhantes não bloqueiam
contas legítimas.

**Compatibilidade:** o modo portátil usa o mesmo banco de produção, mantendo
um backup antes da primeira alteração em cada sessão. A versão instalada
permanece disponível como alternativa durante a migração. Nem todas as telas
especializadas e ações em lote do aplicativo instalado foram migradas ou
validadas ainda; não desinstale a versão antiga até concluir o teste de paridade.

O servidor só escuta em `127.0.0.1`, protege a API com token de sessão e valida
a origem das alterações. Nenhum endpoint de pagamento é publicado na nuvem.

## Testes

`python -m unittest discover -s portable_app -p "test_*.py"`

Os testes criam um banco de exemplo temporário. Não acessam os dados reais.
