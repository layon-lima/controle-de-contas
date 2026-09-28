# Controle de Contas 1.4.3

Correção de regressão no leitor de DARF/ITR em PDF de imagem.

- O mesmo modelo DARF/ITR volta a ser reconhecido mesmo sem camada de texto.
- OCR estrutural confirma RECEITA FEDERAL, pagador CPF/Nome, período, vencimento e valor.
- Regra determinística roda depois do aprendizado para impedir perfil contaminado de sobrescrever dados oficiais.
- DARF/ITR textual também preserva campos determinísticos acima do V1/V2.
- FGTS Digital preserva campos determinísticos acima de perfil aprendido incorreto.
- O PDF IMPOSTO.pdf enviado pelo usuário virou teste obrigatório de regressão.
- Cloudflare/mobile não foi alterado.
