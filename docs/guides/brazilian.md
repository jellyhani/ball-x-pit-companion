# BALL x PIT Companion — Português (Brasil)

[🌐 Languages](../../README.md#choose-your-language)

Assistente não oficial para BALL x PIT no Windows. Lê o estado do jogo e sugere escolhas de nível, fusões, desbloqueios da enciclopédia, coleta e organização da base. Você controla o jogo.

## Instalação

Você precisa do Windows 10/11 e da sua própria instalação de BALL x PIT pelo Steam. Se houver um ZIP em [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases), extraia tudo e execute `BallxPitCompanion.exe`, mantendo a pasta `_internal` ao lado. Se não houver versão publicada, use o código-fonte conforme abaixo. O programa não tem assinatura e pode gerar um aviso do SmartScreen.

Baixe o repositório, abra o PowerShell na pasta, instale o uv e execute a configuração:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Depois, inicie `run_overlay.bat`. A configuração baixa dependências e o BepInEx e extrai textos e ícones do seu jogo. Feche o jogo normalmente para instalar ou atualizar a conexão; a instalação aguarda enquanto ele estiver aberto.

## Como usar

- Confira a conexão nas configurações e abra uma tela de subida de nível ou fusão.
- Ative o modo enciclopédia nas opções de exibição para priorizar descobertas; ele vem desativado.
- Compare a disposição atual com a sugerida e mova os prédios manualmente na ordem indicada.
- Trajetórias e quantidades coletadas são estimativas. Acesso por vários ângulos não significa coletar tudo com um único disparo.

## Privacidade e limites

A conexão apenas lê dados: sem patches Harmony, alterações nos saves ou comandos para o jogo. Registros e dados extraídos ficam em `%LOCALAPPDATA%\BallxPitCompanion`; registros de partidas não são enviados. Versão inicial testada principalmente no Windows 11, 1920×1080, coreano e jogo 1.301. Não garante a melhor disposição nem DPS futuro exato. Nem todas as traduções foram revisadas por falantes nativos. Não é um produto oficial.

## Problemas e relatos

Se a sobreposição não aparecer, confira a visibilidade, a janela e a conexão. Em [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues), informe versões, idioma, resolução, passos e resultados esperado e real. Remova dados privados de imagens e registros; não envie saves nem recursos extraídos do jogo.

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
