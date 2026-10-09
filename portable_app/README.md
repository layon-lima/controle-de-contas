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
PDFs importados neste modo são guardados na pasta
`%LOCALAPPDATA%\ControleContas\documents\portatil`.

**Interface:** tabela renovada sem coluna de ações, duplo clique na conta abre
menu flutuante com ícones originais, filtros por situação/TAG/pesquisa, cadastro,
edição, TAGs, sugestão Cloudflare, cópia/registro de envio sem PDF, pagamento
manual, exclusão com confirmação e abertura/anexo de PDFs.

**Cuidados:** o modo portátil não substitui o aplicativo instalado nesta fase.
OCR automático, conciliação avançada e mesclagem de PDF de comprovante continuam
no desktop clássico até terem uma migração e testes de paridade. O registro de
pagamento manual neste modo é SEM comprovante. Não apague a versão antiga.
Se já tiver um pagamento salvo, o portátil impede registrar outro.

O servidor só escuta em `127.0.0.1`, protege a API com token de sessão e valida
a origem das alterações. Nenhum endpoint de pagamento é publicado na nuvem.

## Testes

`python -m unittest discover -s portable_app -p "test_*.py"`

Os testes criam um banco de exemplo temporário. Não acessam os dados reais.
