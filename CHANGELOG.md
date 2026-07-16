# Changelog

Todas as mudanças relevantes deste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto usa [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.0.1] - 2026-07-15

### Adicionado

- Botão **Excluir da lista** em cada livro, com confirmação e sem apagar dados do LDPlayer ou EPUBs existentes.
- Menu **Restaurar excluídos** para recuperar um livro específico ou todos os itens ocultados.
- Capa tipográfica SVG automática para caches que realmente não contêm uma imagem de capa.
- Testes de regressão para capa ausente e exclusão/restauração persistente da lista.

### Corrigido

- Reconhecimento da capa original quando o capítulo é `Front Cover`, mas a imagem vem rotulada genericamente como `Image`.
- Conversão não é mais interrompida apenas porque a imagem não possui o texto alternativo `cover`.
- Quebra de títulos longos para manter o botão de exclusão visível sem rolagem horizontal.

## [1.0.0] - 2026-07-15

### Adicionado

- Aplicação Windows com interface nativa em português.
- Descoberta automática do ADB do LDPlayer 9 e 14.
- Coleta por ADB root com pausa e retomada do Everand quando necessário.
- Snapshots datados e extração segura de arquivos TAR.
- Catálogo de ebooks com busca, seleção e verificação de acesso local.
- Reconstrução EPUB 3 com capítulos, imagens, tabelas, fontes, links e metadados.
- Navegação EPUB 3, NCX de compatibilidade e lista de páginas.
- Validação interna de ZIP, XML, manifesto, spine, recursos e fragmentos.
- Criptografia pela API CNG do Windows na distribuição confiável.
- Build `Trusted` compatível com Smart App Control.
- Testes automatizados e documentação de uso, arquitetura e release.

[1.0.1]: https://github.com/devfilipeivopereira/everand_converter_epub/releases/tag/v1.0.1
[1.0.0]: https://github.com/devfilipeivopereira/everand_converter_epub/releases/tag/v1.0.0
