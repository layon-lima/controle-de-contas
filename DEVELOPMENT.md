# Fluxo oficial de desenvolvimento

A versão de produção atual é **1.4.7**.

A partir desta estrutura:

1. desenvolvimento acontece na branch `develop`;
2. cada alteração é validada pelo GitHub Actions;
3. quando está pronta, abrimos PR de `develop` para `main`;
4. a `main` representa a versão aprovada;
5. o build gera o atualizador do Windows;
6. o Painel de Controle no PC detecta e instala o artefato aprovado.

Não desenvolver diretamente sobre o instalador do PC. O PC passa a receber versões vindas do GitHub.
