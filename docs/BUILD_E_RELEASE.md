# Build e release

## Requisitos

- Windows 10/11 de 64 bits.
- Python 3.12 oficial para Windows.
- PowerShell.
- Git e GitHub CLI apenas para publicação.

## Ambiente

```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
```

## Testes

Os testes independentes de um cache real podem ser executados diretamente:

```powershell
.\.venv-build\Scripts\python.exe -m unittest discover -s tests -v
```

Para incluir catálogo, direitos e conversão integral, aponte para um snapshot local autorizado:

```powershell
$env:EVERAND_TEST_LIBRARY = 'C:\caminho\para\snapshot'
.\.venv-build\Scripts\python.exe -m unittest discover -s tests -v
```

Nunca coloque esse snapshot dentro do commit.

## Gerar as distribuições

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
powershell -ExecutionPolicy Bypass -File .\build_trusted_windows.ps1
```

O primeiro script cria a distribuição convencional e os componentes necessários. O segundo:

1. confirma a assinatura do runtime oficial do Python;
2. copia somente os módulos Qt necessários;
3. inclui o código e o manual;
4. valida todos os arquivos `.exe`, `.dll` e `.pyd`;
5. cria `release/EverandEPUBStudio-Trusted.zip`.

## Checklist de release

- [ ] Versão atualizada em `everand_app/version.py`.
- [ ] `CHANGELOG.md` e notas da versão atualizados.
- [ ] Testes automatizados aprovados.
- [ ] Coleta, conversão e interface testadas pelo executável final.
- [ ] EPUB aprovado pelo EPUBCheck.
- [ ] Zero componentes nativos com assinatura inválida no pacote `Trusted`.
- [ ] ZIP contém executável, manual e runtime completo.
- [ ] `SHA256SUMS.txt` corresponde exatamente aos artefatos publicados.
- [ ] Repositório não contém bancos, chaves, snapshots ou EPUBs.
