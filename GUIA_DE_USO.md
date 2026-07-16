# Everand EPUB Studio 1.0.1

Aplicativo Windows para coletar ebooks baixados para leitura offline no Everand dentro do LDPlayer e reconstruí-los como EPUB 3. O processamento é totalmente local.

## Início rápido

1. Inicie o LDPlayer 9 ou 14.
2. Nas configurações do LDPlayer, habilite **ROOT** e **Depuração ADB**; reinicie o emulador se ele solicitar.
3. Abra o Everand, entre na sua conta e baixe o ebook para leitura offline. Abra o livro ao menos uma vez para concluir o cache.
4. Extraia por completo `EverandEPUBStudio-Trusted.zip`. O executável depende das pastas que vêm ao lado dele.
5. Execute `EverandEPUBStudio.exe`.
6. Clique em **Atualizar do LDPlayer**.
7. Escolha a pasta de saída, marque os livros desejados e clique em **Reconstruir EPUB selecionados**.

O Everand é pausado por alguns segundos durante a cópia consistente dos bancos e reaberto automaticamente ao final.

## O que o aplicativo faz

- Localiza automaticamente o ADB fornecido pelo LDPlayer, sem depender do Epubor.
- Confirma acesso root ao diretório privado do Everand.
- Copia o cache de documentos, a tabela local de chaves e os dois bancos de metadados/acesso.
- Cria snapshots datados sem apagar as coletas anteriores.
- Lista apenas ebooks EPUB presentes no cache offline.
- Só converte quando os bancos locais confirmam `unlocked`, acesso integral e permissão de download.
- Reconstrói capítulos, estilos, imagens, tabelas, links, navegação, páginas, capa, fontes e metadados.
- Identifica a imagem no capítulo de capa mesmo quando ela não está rotulada como `cover`.
- Cria uma capa tipográfica válida com título e autor quando nenhuma imagem de capa existe.
- Valida a estrutura interna antes de considerar o EPUB concluído.

## Organizar a lista de livros

- Clique em **Excluir da lista** no cartão de um livro para ocultá-lo.
- A confirmação deixa claro que o download no LDPlayer e os EPUBs já gerados não serão apagados.
- Clique em **Restaurar excluídos** na barra lateral para restaurar um título específico ou todos.
- A preferência permanece salva após fechar o programa e pode ser desfeita a qualquer momento.

## Pastas usadas

- Snapshots: `%LOCALAPPDATA%\Everand EPUB Studio\snapshots`
- Logs: `%LOCALAPPDATA%\Everand EPUB Studio\logs`
- Saída padrão: `%USERPROFILE%\Documents\EPUBs Everand`

O cache dentro do LDPlayer não é alterado. Apenas arquivos temporários próprios são criados em `/data/local/tmp/everand-epub-studio-*` e removidos ao final.

## Segurança e privacidade

- Nenhuma credencial, chave ou conteúdo é enviado para a internet.
- Pacotes vindos do emulador passam por proteção contra travessia de diretórios e links inseguros.
- A edição `Trusted` usa um runtime oficial assinado pela Python Software Foundation e componentes Qt assinados.
- A decifragem usa a API criptográfica CNG do próprio Windows; não há extensão criptográfica nativa sem assinatura.
- O aplicativo é destinado somente a downloads offline autorizados da conta do usuário. Ele recusa itens sem direito local confirmado.

## Limitações atuais

- Suporta ebooks EPUB; audiobooks não são coletados nem convertidos.
- Requer Windows, LDPlayer com ROOT/ADB e o Everand instalado como `com.scribd.app.reader0`.
- O livro precisa estar completamente baixado para leitura offline.
- Mudanças futuras no formato interno do Everand podem exigir atualização do conversor.
- Um livro pode continuar indisponível quando a assinatura da conta não concede acesso integral ou download.

## Diagnóstico rápido

- **LDPlayer não encontrado:** inicie o emulador e confirme a depuração ADB.
- **Sem acesso root:** habilite ROOT nas configurações do LDPlayer e reinicie-o.
- **Everand não instalado:** instale o aplicativo no mesmo emulador conectado.
- **Livro não aparece:** conclua o download offline e abra o livro uma vez no Everand.
- **Livro não elegível:** verifique se sua conta possui acesso integral e permissão de download.
- **Livro sem capa identificada:** a versão 1.0.1 procura a imagem no capítulo de capa e, se ela realmente não existir, cria uma capa tipográfica automaticamente sem interromper a conversão.
- **Falha inesperada:** consulte `%LOCALAPPDATA%\Everand EPUB Studio\logs\last-run.log`.

## Compatibilidade com Smart App Control

O Windows desta máquina usa Smart App Control em modo de imposição e bloqueia executáveis novos sem assinatura de uma autoridade confiável. Por isso, a distribuição recomendada é `EverandEPUBStudio-Trusted`. Ela preserva as assinaturas digitais dos componentes nativos e foi executada com sucesso sob essa política.

Não desative o Smart App Control para usar o aplicativo.
