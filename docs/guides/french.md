# BALL x PIT Companion — Français

[🌐 Languages](../../README.md#choose-your-language)

Un assistant Windows non officiel pour BALL x PIT. Il lit l’état du jeu et conseille les choix de niveau, les fusions, les découvertes de l’encyclopédie, la récolte et le placement de la base. Vous gardez le contrôle du jeu.

## Installation

Il faut Windows 10/11 et votre propre installation Steam de BALL x PIT. Si une archive ZIP est disponible dans [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases), extrayez-la entièrement et lancez `BallxPitCompanion.exe`, avec le dossier `_internal` à ses côtés. Sinon, utilisez le code source ci-dessous. Le programme n’est pas signé et peut déclencher SmartScreen.

Téléchargez le dépôt, ouvrez PowerShell dans son dossier, installez uv et lancez la configuration :

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Lancez ensuite `run_overlay.bat`. La configuration télécharge les dépendances et BepInEx, puis extrait les textes et icônes de votre jeu. Quittez normalement le jeu pour installer ou mettre à jour la passerelle ; l’installation attend tant que le jeu tourne.

## Utilisation

- Vérifiez la connexion dans les paramètres, puis ouvrez un écran de montée de niveau ou de fusion.
- Activez le mode encyclopédie dans les réglages d’affichage pour privilégier les découvertes ; il est désactivé par défaut.
- Comparez la disposition actuelle et la proposition, puis effectuez les déplacements manuellement dans l’ordre indiqué.
- Trajectoires et récoltes sont estimées. L’accès depuis plusieurs angles ne signifie pas qu’un seul tir récoltera tout.

## Confidentialité et limites

La passerelle lit uniquement : aucun patch Harmony, aucune modification de sauvegarde ni commande envoyée au jeu. Les journaux et données extraites restent dans `%LOCALAPPDATA%\BallxPitCompanion` ; les parties ne sont pas téléversées. Cette version préliminaire a surtout été testée sous Windows 11, en 1920×1080, en coréen, avec le jeu 1.301. Ni la disposition optimale ni les futurs DPS exacts ne sont garantis. Toutes les traductions n’ont pas été relues par des locuteurs natifs. Produit non officiel.

## Dépannage et signalement

Si la surcouche manque, vérifiez sa visibilité, la fenêtre du jeu et la connexion. Dans [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues), indiquez versions, langue, résolution, étapes et résultats attendu et observé. Retirez les données privées des captures et journaux ; ne joignez ni sauvegardes ni ressources extraites du jeu.

## Droits, responsabilité et confidentialité

Cet outil non officiel est fourni en l’état. Les droits sur le jeu et les contenus tiers appartiennent à leurs titulaires respectifs. Les droits légalement non excluables restent applicables.

[Full notice / English · 한국어](../../DISCLAIMER.md) · [Privacy / English · 한국어](../../PRIVACY.md)

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
