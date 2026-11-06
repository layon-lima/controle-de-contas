# WhatsApp: card nativo com copiar Pix/boleto

Esta integracao nao envia nada automaticamente e **nao** substitui o botao
atual "Enviar para pagamento" que copia o texto.

## 1. Habilitar Cloud API + coexistencia

- O WhatsApp Business aplicativo, sozinho, nao fornece Cloud API.
- Para usar o mesmo numero no aplicativo e na Cloud API, configurar **Coexistencia**
  via Embedded Signup de parceiro/Tech Provider Meta, se o numero for elegivel.
- Obter **Phone Number ID**, **token de acesso** apto a enviar mensagens pelo
  numero e habilitacao de **Payment Request CTA templates** para WABA brasileiro.
- Nunca compartilhar token em chat, print, issue ou commit. O arquivo de configuracao
  e armazenado **somente** em `/opt/apps/contas-runtime/whatsapp-api.json`.

Documentacao da Meta: https://developers.facebook.com/documentation/business-messaging/whatsapp/payments/payments-br/payment-request-cta

## 2. Criar templates aprovados (categoria Utility, se a Meta aprovar)

Criar dois modelos no WhatsApp Manager com corpo exatamente com **3 variaveis**:

```text
RECEBEDOR: {{1}}

VALOR: {{2}}

{{3}}
```

- **Boleto**: botao **PAYMENT_REQUEST** do tipo **Copy Boleto code**
  com codigo fornecido em cada envio; sugestao de nome: `contas_pagar_boleto`.
- **Pix**: botao **PAYMENT_REQUEST** do tipo **Copy Pix code**
  com codigo fornecido em cada envio; sugestao de nome: `contas_pagar_pix`.
- Os nomes precisam ser os dos templates aprovados na conta Meta, nao apenas
  nomes desejados. Os botões podem estar indisponiveis em WABAs inelegiveis.
- O endpoint nao cria templates por conta propria nem contorna aprovacao.

## 3. Configurar credencial na VPS

```bash
cd /opt/apps/contas && python3 tools/CONFIGURAR_WHATSAPP_META.py
```

O script pergunta os dados e **oculta o token digitado**, grava arquivo privado
com `0600` no volume persistente. A pasta e a configuracao de R2 nao mudam.
O container ja monta `/opt/apps/contas-runtime` em `/data/ControleContas`.

## 4. Usar

No menu da conta aberta, clicar em **Enviar card pelo WhatsApp**. Informar
o numero com DDD, marcar a confirmacao de autorizacao do destinatario e
confirmar o envio uma segunda vez.

- Aceita apenas linhas digitaveis de 47/48 digitos ou Pix copia-e-cola no
  formato EMV (iniciado por `000201`).
- O `.pdf` final da observacao sai apenas do texto enviado.
- A API confirma aceitacao, nao entrega ao destinatario.
- O status da conta nao e alterado e nao marca a conta como enviada/paga.
- O card so e enviado se o modelo tiver sido aprovado pela Meta e o numero
  remetente estiver autorizado a utilizar o recurso. Teste inicialmente em
  um numero de confianca que aceitou receber a mensagem.
- Envio de template pode ser tarifado pela Meta.

**Nao serve para** compartilhar cartoes interativos como mensagem do WhatsApp
aplicativo sem API; a API oficial envia pelo numero configurado.

## 5. Diagnosticos

- Botao informa "API ainda nao conectado": faltam credenciais/modelos aprovados.
- "Conta ja paga": envio bloqueado intencionalmente.
- "Modelo ...": conferir status do template aprovado no Meta Manager.
- Codigo de 44 digitos ou chave Pix comum: manter o botao tradicional de texto.
- "Meta nao confirmou": nao assumir entrega e nao reenviar antes de conferir.
