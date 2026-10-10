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
todos os períodos, menu por duplo clique, filtros por situação/pesquisa,
cadastro/edição, sugestão Cloudflare, cópia com confirmação de envio,
pagamento manual, exclusão e leitura/anexo/visualização de PDFs.
Também permite marcar várias contas diretamente na tabela e sugerir, copiar
para pagamento ou registrar pagamento manual em lote. Ao copiar várias,
o banco só recebe a data de envio após confirmação do clipboard.

**OCR híbrido conservador (ATT 018):** na barra superior, **Leitura**
permite escolher `OCR aprimorado` (padrão) ou `OCR original` (retorno
imediato ao comportamento anterior, sem reinstalação). O aprimorado SEMPRE
executa primeiro o motor original e preserva todos os campos que ele
preencheu. Uma segunda análise limitada consulta o texto digital ou,
somente quando faltam ao menos dois campos críticos num PDF escaneado,
faz OCR de até duas páginas com contraste tratado e limite de tempo.
As sugestões são aceitas apenas para campos vazios e com rótulos
explícitos: fornecedor, pagador, valor em reais, vencimento válido,
linha digitável bancária/arrecadação validada ou PIX EMV com CRC válido.
Valores conflitantes são destacados para conferência, jamais substituídos
automaticamente. O OCR complementar não escreve banco, perfis ou PDFs.
Quando falha, retorna o mesmo resultado original com um aviso; não interrompe
o cadastro. A escolha também funciona ao reler um boleto existente.

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
permanece disponível como alternativa durante a migração. As operações em lote principais já foram portadas, mas as demais telas
especializadas e integrações precisam passar por validação real antes de
desinstalar a versão antiga. Não assuma equivalência total apenas pela interface.

O servidor só escuta em `127.0.0.1`, protege a API com token de sessão e valida
a origem das alterações. Nenhum endpoint de pagamento é publicado na nuvem.

## Testes

`python -m unittest discover -s portable_app -p "test_*.py"`

Os testes criam um banco de exemplo temporário. Não acessam os dados reais.

## Entrada unica e retirada do executavel instalado
Apos concluir os testes do painel de atualizacao, o atalho da area de trabalho
**Controle de Contas** passa a iniciar a versao web com um icone ICO proprio
criado localmente. O arquivo VBS na raiz continua sendo o iniciador unico.
O atualizador tenta desinstalar o EXE legado via seu desinstalador oficial
**somente** quando: web abre na revisao esperada, testes passam, mobile e
sincronizador sao verificados, atalho web aponta para o VBS e o OCR preservado
possui binario Tesseract independente e dados do idioma portugues.

Nenhum processo antigo sera finalizado forcadamente. Se o app EXE estiver
aberto ou faltar algum requisito, a desinstalacao e adiada com aviso no log,
sem remover dados. Nao se apagam o banco em LOCALAPPDATA/ControleContas,
os PDFs em pastas do usuario, os perfis de aprendizagem, backups ou
as partes de codigo legado que o OCR web ainda reutiliza.

Interface: arrastar um unico PDF sobre a pagina oferece cadastro novo com
leitura aprimorada e confirmacao antes de salvar. O seletor de OCR foi removido;
a leitura aprimorada e o padrao unico visivel. Em telas estreitas, a listagem
assume cartoes sem deslocamento horizontal. A funcao interna de leitura
original e mantida como parte/fallback do leitor aprimorado.

## Nome automático dos PDFs cadastrados
No cadastro por **Importar PDF** ou arrastar e soltar, Observações começa com
o nome original do PDF. Se o usuário trocar Observações antes de confirmar,
esse será o nome físico do documento salvo (com extensão .pdf), tanto na pasta
BOLETOS organizada quanto no armazenamento provisório. Se Observações ficar
em branco ao confirmar, o sistema reutiliza o nome de origem do PDF e o
registra no cadastro; jamais salva um arquivo sem nome. Nomes duplicados
recebem sufixo numérico, sem sobrescrever arquivos anteriores.
A mesclagem de comprovante preserva o caminho e o nome do PDF cadastrado;
a releitura OCR de conta existente não substitui automaticamente Observações.

## Remoção de TAGs
A interface web e sua API não oferecem mais filtros, colunas, menus ou cadastros de TAGs. Eventuais tabelas antigas `cc_tags` e `cc_account_tags` são mantidas intactas no banco para não destruir dados históricos durante a migração.

## OCR: revisao e aprendizado verificavel
O OCR V2 prioriza a regiao precisa que foi confirmada, evitando escolher
um valor incorreto quando ha varios numeros na mesma linha. Perfis historicos
praticamente identicos podem resolver empate estrutural de modo conservador.
Corrigir o fornecedor, pagador, vencimento, valor, referencia ou codigo
em **Editar conta** com PDF existente agora tambem executa o aprendizado
apos salvar; anteriormente somente a confirmacao do novo PDF treinava.
Se o aprendizado nao conseguir localizar a correcao no arquivo, o cadastro
fica salvo sem perda e a interface mostra um aviso explicito por 10 segundos.
O app nao garante aprender dados que nao estejam legiveis no boleto.

## Colunas ajustaveis e sem selecao em lote
No desktop (>990px), arraste a borda direita dos titulos para aumentar ou reduzir cada coluna, com limite minimo. A coluna cresce para revelar o texto truncado; fonte, conteudo e alinhamento a esquerda nao mudam e a linha nao quebra. O tamanho escolhido persiste no navegador e pode ser restaurado com **Restaurar colunas**. Expansao intencional alem da tela habilita o scroll horizontal da tabela; em telas pequenas seguem os cards responsivos. Foram removidos quadrados e a barra de selecao em lote; ficam os menus individuais, pesquisa, filtros e pagamentos. Nenhum dado do banco e alterado.
