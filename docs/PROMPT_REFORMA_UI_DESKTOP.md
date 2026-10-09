# PROMPT — REFORMA DA INTERFACE DESKTOP (CONTROLE DE CONTAS)

Implemente no aplicativo Windows real (PySide6) o layout aprovado na imagem fornecida pelo usuário em 09/10/2026: dashboard claro, moderno, barra inferior, nenhuma sidebar, tabela central prioritária.

## Layout obrigatório
- Cabeçalho horizontal com nome do app, pesquisa global existente, ação funcional Importar PDF, acesso mobile e Nova conta, sem controles duplicados.
- Quatro cards: Vencidas, Próximas, Em dia, Pagas (este mês). Altura menor que na imagem de referência: 72–78px, números e valores legíveis.
- Tabela clara com fornecedor/avatares de iniciais, vencimento, valor, status visual, sugerido em, enviado em e ações reais por linha.
- Ações em lote já existentes no topo da lista, botão original Registrar pagamento reaproveitado.
- Barra de navegação inferior horizontal: Visão geral, Contas, Pagamentos, Relatórios, Configurações. Sem menu lateral e sem abas redundantes.

## Regras funcionais
1. Reutilize widgets, handlers, sinais, banco, modelos, OCR e cloud já existentes. Não recrie fluxos de negócio.
2. Importar PDF deve chamar o método existente processar_pdf_solto(caminho), sem novo parser.
3. Registrar pagamento e ações da tabela devem reutilizar botões/métodos originais, mantendo seleção de conta correta.
4. Preservar Sugerido e Enviado como eventos separados; a reforma visual não altera o banco.
5. Instalar apenas um novo chrome visual; não criar barra lateral, funções duplicadas nem loops de redesenho a cada 5s.
6. Sem sobrescrever estilos de diálogos, sem botões apagados, com hierarquia visual adequada e boa responsividade.
7. Não mudar patches de OCR/PDF, dados, paths, sincronização ou arquivos compilados.
8. Trabalhar em develop. Fazer validação sintática e smoke test real antes de declarar pronto.

## Aceite
Interface desktop fiel ao mockup com cards compactos, bottom navigation funcional, tabela bonita e legível e ações reais; sem regressões em contas, PDF, pagamentos, histórico, aprendizado ou mobile.