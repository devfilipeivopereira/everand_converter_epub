# Política de segurança

## Versões suportadas

| Versão | Suporte |
|---|---|
| 1.0.x | Sim |

## Relatar uma vulnerabilidade

Não publique detalhes exploráveis em uma issue aberta. Use a opção **Security → Report a vulnerability** deste repositório para enviar um relatório privado.

Inclua, quando possível:

- versão do aplicativo e do Windows;
- versão do LDPlayer e do Android;
- descrição do impacto;
- passos mínimos para reprodução;
- logs sem conteúdo de livros, credenciais, bancos ou chaves;
- sugestão de correção, se houver.

O projeto não solicita nem precisa de credenciais do Everand. Nunca envie tokens, cookies, bancos privados, `FILENAME_KEYS.xml`, snapshots ou ebooks em relatórios.

## Escopo de segurança

São especialmente relevantes problemas envolvendo:

- travessia de diretórios durante extração TAR;
- execução arbitrária de comandos ADB;
- substituição insegura de arquivos de saída;
- vazamento de conteúdo, chaves ou bancos locais;
- carregamento de bibliotecas nativas não confiáveis;
- conversão de itens sem acesso integral confirmado.
