CONTROLE DE CONTAS - PROJETO RECUPERADO
======================================

O núcleo original foi recuperado do executável instalado em bytecode Python 3.13.
Por isso, esta versão de recuperação deve ser executada/empacotada com Python 3.13.

Correções adicionadas em controle_contas/patches.py:
1. Cadastro de conta não modal: não usa mais QDialog.exec() para Nova Conta,
   Editar Conta e conta criada ao soltar/importar um PDF.
2. Ao carregar outro PDF, campos que já possuem conteúdo digitado são preservados.
   Vencimento e forma de pagamento também respeitam alterações manuais.
3. PDFs escaneados: se a camada de texto estiver ausente/insuficiente, é tentado OCR.

OCR
---
O patch usa Tesseract OCR através do pacote pytesseract.
Ele procura tesseract.exe nestes locais:
- PATH do Windows;
- C:\Program Files\Tesseract-OCR\tesseract.exe;
- C:\Program Files (x86)\Tesseract-OCR\tesseract.exe;
- pasta tesseract ao lado do projeto/aplicativo empacotado.

Se o Tesseract não estiver disponível, PDFs digitais continuam funcionando normalmente,
mas PDFs apenas escaneados não terão OCR até o motor ser instalado/empacotado.

IMPORTANTE
----------
Os arquivos .pyc originais recebidos também foram preservados em originais_compilados.
Não apague essa pasta até termos uma versão fonte totalmente reconstruída.
