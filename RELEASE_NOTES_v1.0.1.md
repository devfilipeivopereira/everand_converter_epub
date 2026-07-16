# Everand EPUB Studio 1.0.1

Versão corretiva que elimina a falha de capa vista em alguns livros e adiciona organização reversível do catálogo.

## Correções

- O conversor agora reconhece a primeira imagem do capítulo `Front Cover` como capa, mesmo quando o cache usa o rótulo genérico `Image`.
- Se o cache realmente não possuir capa, o aplicativo gera uma capa tipográfica SVG com título e autor e continua a conversão.
- Títulos longos quebram linha corretamente e não escondem as ações do cartão.

## Catálogo

- Cada livro possui o botão **Excluir da lista**.
- A exclusão apenas oculta o item; nenhum arquivo do LDPlayer e nenhum EPUB existente é apagado.
- **Restaurar excluídos** permite recuperar um título específico ou todos os itens ocultados.

## Verificação

- Falha original reproduzida com *Comeback Churches: How 300 Churches Turned Around and Yours Can, Too*.
- A imagem correta da edição foi inferida no capítulo `Front Cover` e usada como capa.
- 10 testes automatizados aprovados no ambiente integral.
- EPUBCheck 5.3.0 aprovou EPUBs com capa original inferida e capa automática com zero erros, zero avisos e zero informações.

## Download recomendado

Baixe `EverandEPUBStudio-Trusted.zip`, extraia o pacote inteiro e execute `EverandEPUBStudio.exe` dentro da pasta extraída.

O `.exe` publicado separadamente não é autônomo; ele depende das pastas incluídas no ZIP.
