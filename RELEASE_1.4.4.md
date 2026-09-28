# Controle de Contas 1.4.4

Correção da causa real da regressão em DARF/ITR escaneado.

- O erro exato foi reproduzido forçando OCR somente ENG: Recebedor vazio, Pagador "até", Valor 0 e Vencimento vazio.
- O parser DARF agora tolera erros típicos do OCR em inglês, como "arrecadagao", "apuragao" e "composigao".
- O Tesseract empacotado passa a ter prioridade sobre instalações externas encontradas no PATH.
- O snapshot inclui `por.traineddata` e o workflow valida que ele entrou no aplicativo empacotado.
- O mesmo IMPOSTO.pdf passa mesmo quando o teste força OCR somente ENG.
- Regressão validada também com BB, DARF Davi, Equatorial, FGTS e ITR usando os perfis aprendidos existentes.
- Mobile/Cloudflare permanece inalterado.
