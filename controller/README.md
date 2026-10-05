# Painel de Controle — Controle de Contas

Painel local para operar o aplicativo e transformar o GitHub na fonte oficial de desenvolvimento e atualização.

## Rodar

Na raiz do repositório:

```powershell
python .\controller\server.py
```

O navegador abre em:

`http://127.0.0.1:8766`

## O que esta primeira versão já faz

- mostra se o Controle de Contas está aberto;
- identifica a versão instalada pelo executável;
- consulta a `main` e o último build aprovado usando o GitHub CLI;
- mostra se existe atualização disponível;
- abre, fecha e reinicia o aplicativo;
- baixa o artefato aprovado do GitHub Actions e inicia o atualizador;
- mostra as últimas linhas do `cloud_sync.log`;
- funciona somente em `127.0.0.1`.

## Requisito para GitHub privado

O PC precisa ter o GitHub CLI instalado e autenticado:

```powershell
gh auth status
```

Nenhum token fica salvo no código do painel.

## Configuração local

Na primeira execução é criado:

`%LOCALAPPDATA%\ControleContas\controller.json`

Se o executável não for encontrado automaticamente, preencha `app_executable` com o caminho completo do programa instalado.
