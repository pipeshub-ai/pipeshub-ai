# 🌐 Translations (i18n)

This directory holds community translations of PipesHub's top-level documentation.

The **English** documents in the repository root are the **canonical source of truth**. Translations are provided for convenience; if a translation is out of date or conflicts with the English version, the English version wins. Pull requests that update a translation to match the latest English text are very welcome.

## Available languages

| Language | Code | README | CONTRIBUTING | CODE OF CONDUCT | SECURITY |
|----------|------|--------|--------------|-----------------|----------|
| English (canonical) | `en` | [README](../../README.md) | [CONTRIBUTING](../../CONTRIBUTING.md) | [CODE_OF_CONDUCT](../../CODE_OF_CONDUCT.md) | [SECURITY](../../SECURITY.md) |
| Français | `fr` | [README](fr/README.md) | [CONTRIBUTING](fr/CONTRIBUTING.md) | [Code de Conduite](fr/CODE_OF_CONDUCT.md) | [Sécurité](fr/SECURITY.md) |
| Deutsch | `de` | [README](de/README.md) | [CONTRIBUTING](de/CONTRIBUTING.md) | [Verhaltenskodex](de/CODE_OF_CONDUCT.md) | [Sicherheit](de/SECURITY.md) |
| 简体中文 | `zh-CN` | [README](zh-CN/README.md) | [CONTRIBUTING](zh-CN/CONTRIBUTING.md) | [行为准则](zh-CN/CODE_OF_CONDUCT.md) | [安全](zh-CN/SECURITY.md) |
| 日本語 | `ja` | [README](ja/README.md) | [CONTRIBUTING](ja/CONTRIBUTING.md) | [行動規範](ja/CODE_OF_CONDUCT.md) | [セキュリティ](ja/SECURITY.md) |
| Русский | `ru` | [README](ru/README.md) | [CONTRIBUTING](ru/CONTRIBUTING.md) | [Кодекс поведения](ru/CODE_OF_CONDUCT.md) | [Безопасность](ru/SECURITY.md) |
| עברית | `he` | [README](he/README.md) | [CONTRIBUTING](he/CONTRIBUTING.md) | [כללי התנהגות](he/CODE_OF_CONDUCT.md) | [אבטחה](he/SECURITY.md) |
| 한국어 | `ko` | [README](ko/README.md) | [CONTRIBUTING](ko/CONTRIBUTING.md) | [행동 강령](ko/CODE_OF_CONDUCT.md) | [보안](ko/SECURITY.md) |
| Español | `es` | [README](es/README.md) | [CONTRIBUTING](es/CONTRIBUTING.md) | [Código de Conducta](es/CODE_OF_CONDUCT.md) | [Seguridad](es/SECURITY.md) |
| Português | `pt` | [README](pt/README.md) | [CONTRIBUTING](pt/CONTRIBUTING.md) | [Código de Conduta](pt/CODE_OF_CONDUCT.md) | [Segurança](pt/SECURITY.md) |
| Türkçe | `tr` | [README](tr/README.md) | [CONTRIBUTING](tr/CONTRIBUTING.md) | [Davranış Kuralları](tr/CODE_OF_CONDUCT.md) | [Güvenlik](tr/SECURITY.md) |
| Tiếng Việt | `vi` | [README](vi/README.md) | [CONTRIBUTING](vi/CONTRIBUTING.md) | [Quy tắc ứng xử](vi/CODE_OF_CONDUCT.md) | [Bảo mật](vi/SECURITY.md) |
| Italiano | `it` | [README](it/README.md) | [CONTRIBUTING](it/CONTRIBUTING.md) | [Codice di Condotta](it/CODE_OF_CONDUCT.md) | [Sicurezza](it/SECURITY.md) |

## Contributing a translation

We welcome new translations and fixes to existing ones.

1. Translations live under `docs/i18n/<code>/`, where `<code>` is a BCP-47 language code (for example `ja`, `pt`, `zh-CN`).
2. Mirror the structure of the English documents — same headings, same order, same links.
3. **Do not translate** code blocks, shell commands, environment variable names, URLs, badges, file paths, or product/technology names (PipesHub, Docker, Qdrant, Kafka, etc.).
4. Keep the **Translations:** switcher row at the top of each document.
5. Right-to-left languages (Hebrew `he`) wrap their body in `<div dir="rtl">` so the prose renders correctly on GitHub.
6. If you add a new language, add a row to the table above and add a link to the **Translations:** switcher in every English document at the repository root.

> Questions about translations? Open an issue or reach out on [Discord](https://discord.com/invite/K5RskzJBm2).
