# Everand EPUB Studio

[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4?logo=windows)](https://github.com/devfilipeivopereira/everand_converter_epub/releases/latest)
[![Release](https://img.shields.io/github/v/release/devfilipeivopereira/everand_converter_epub)](https://github.com/devfilipeivopereira/everand_converter_epub/releases/latest)

Aplicativo Windows para coletar ebooks que já foram baixados para leitura offline no Everand dentro do LDPlayer e reconstruí-los como EPUB 3 validado. Todo o processamento acontece localmente.

> [!IMPORTANT]
> Este projeto destina-se exclusivamente a conteúdo acessado legitimamente pela conta do próprio usuário. A conversão é recusada quando os bancos locais não confirmam acesso integral e permissão de download.

## Download

Baixe a versão mais recente na página de [Releases](https://github.com/devfilipeivopereira/everand_converter_epub/releases/latest).

Use preferencialmente `EverandEPUBStudio-Trusted.zip`, extraia o pacote inteiro e execute `EverandEPUBStudio.exe` dentro da pasta extraída. O executável depende dos componentes distribuídos ao lado dele.

## Recursos

- Descoberta automática do ADB fornecido pelo LDPlayer 9 ou 14.
- Coleta consistente do cache privado do Everand por ADB root.
- Snapshots datados, sem apagar coletas anteriores.
- Catálogo com busca, seleção, exclusão reversível e situação de acesso de cada ebook.
- Verificação local de acesso integral e permissão de download.
- Reconstrução de capítulos, formatação por fragmento, imagens, tabelas e links.
- Capa original detectada mesmo sem rótulo explícito e capa tipográfica automática quando a imagem não existe.
- Fontes, navegação EPUB 3, NCX, lista de páginas e metadados bibliográficos.
- Validação estrutural antes de publicar o arquivo final.
- Interface nativa em português e processamento em segundo plano.
- Edição compatível com Smart App Control sem desativar a proteção do Windows.

## Uso rápido

1. Inicie o LDPlayer e habilite **ROOT** e **Depuração ADB** nas configurações.
2. No Everand, baixe o ebook para leitura offline e abra-o uma vez.
3. Extraia completamente o ZIP da release.
4. Execute `EverandEPUBStudio.exe`.
5. Clique em **Atualizar do LDPlayer**.
6. Escolha a pasta de saída e clique em **Reconstruir EPUB selecionados**.

Cada cartão possui **Excluir da lista**. Essa ação só oculta o item no aplicativo; não apaga o download no LDPlayer nem EPUBs existentes. Use **Restaurar excluídos** na barra lateral para desfazer.

Consulte o [guia de uso](GUIA_DE_USO.md) para configuração, diagnóstico e localização dos snapshots.

## Segurança e privacidade

- Nenhum conteúdo, credencial ou chave é enviado pela rede.
- Dados recebidos do emulador são validados contra travessia de diretórios e links inseguros.
- O cache original no LDPlayer não é modificado.
- EPUBs são gravados de forma atômica: uma falha não substitui um arquivo válido anterior.
- Bancos, chaves, caches, snapshots e EPUBs são bloqueados pelo `.gitignore`.
- A edição `Trusted` verifica as assinaturas de todos os componentes nativos durante o build.

Leia [SECURITY.md](SECURITY.md) para relatar vulnerabilidades com responsabilidade.

## Compatibilidade

- Windows 10 ou 11 de 64 bits.
- LDPlayer 9 ou 14 com ROOT/ADB habilitados.
- Everand instalado como `com.scribd.app.reader0`.
- Ebooks integralmente baixados para leitura offline.

Audiobooks não são suportados nesta versão. Mudanças futuras no armazenamento interno do Everand podem exigir atualização do conversor.

## Desenvolvimento

O projeto usa Python 3.12, PySide6, ADB e a API CNG do Windows.

```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-build\Scripts\python.exe -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
powershell -ExecutionPolicy Bypass -File .\build_trusted_windows.ps1
```

Detalhes adicionais:

- [Arquitetura](docs/ARQUITETURA.md)
- [Processo de build e release](docs/BUILD_E_RELEASE.md)
- [Histórico de mudanças](CHANGELOG.md)

## Validação da versão 1.0.1

- Conversão reproduzida e corrigida com o livro *Comeback Churches*, cuja capa original vinha rotulada apenas como `Image`.
- 10 testes automatizados aprovados no ambiente integral, incluindo exclusão/restauração e capa ausente.
- 96 componentes nativos com assinatura digital válida na edição `Trusted`.
- EPUBCheck 5.3.0: zero erros, zero avisos e zero informações tanto com a capa original inferida quanto com a capa automática.

## Aviso

Everand e Scribd são marcas de seus respectivos proprietários. Este projeto independente não é afiliado, patrocinado ou endossado por eles. O usuário é responsável por observar os termos aplicáveis e a legislação de sua jurisdição.
