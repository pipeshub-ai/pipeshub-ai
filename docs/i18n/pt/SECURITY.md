# Política de segurança

<div align="center">

**Translations:** [English](../../../SECURITY.md) · [Français](../fr/SECURITY.md) · [Deutsch](../de/SECURITY.md) · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · [Español](../es/SECURITY.md) · **Português** · [Türkçe](../tr/SECURITY.md) · [Tiếng Việt](../vi/SECURITY.md) · [Italiano](../it/SECURITY.md)

</div>

## Relatar uma vulnerabilidade

Levamos as vulnerabilidades de segurança a sério e agradecemos seus esforços para divulgar de forma responsável quaisquer problemas que você encontrar.

### Como relatar

**Por favor, NÃO crie issues públicas no GitHub para vulnerabilidades de segurança.**

Em vez disso, relate as vulnerabilidades de segurança enviando um e-mail para: **abhishek@pipeshub.com**

### O que incluir

Ao relatar uma vulnerabilidade, inclua:

- Descrição da vulnerabilidade
- Passos para reproduzir o problema
- Avaliação do impacto potencial
- Correção sugerida (se disponível)
- Suas informações de contato para acompanhamento

### O que esperar

- **Resposta inicial**: Confirmaremos o recebimento do seu relato em até 48 horas
- **Avaliação**: Forneceremos uma avaliação inicial em até 5 dias úteis
- **Atualizações**: Enviaremos atualizações de progresso a cada 7 dias até a resolução
- **Resolução**: Buscamos resolver vulnerabilidades críticas em até 30 dias
- **Crédito**: Reconheceremos sua contribuição em nossos avisos de segurança (a menos que você prefira permanecer anônimo)

### Cronograma de divulgação

- **Dia 0**: Vulnerabilidade relatada
- **Dia 1-2**: Confirmação inicial
- **Dia 1-5**: Triagem inicial e avaliação de impacto
- **Dia 1-30**: Desenvolvimento e teste da correção
- **Dia 30+**: Divulgação pública após a implantação da correção, normalmente dentro de 90 dias a partir do relato inicial.

### Avaliação de vulnerabilidades

Classificamos as vulnerabilidades de acordo com os seguintes critérios:

- **Crítica**: Ameaça imediata aos dados do usuário ou à integridade do sistema
- **Alta**: Impacto de segurança significativo com ampla exposição de usuários
- **Média**: Impacto de segurança moderado com exposição limitada
- **Baixa**: Problemas de segurança menores com impacto mínimo

### Boas práticas de segurança

Ao contribuir para este projeto:

- Mantenha as dependências atualizadas
- Siga práticas de codificação segura
- Valide todas as entradas do usuário
- Use autenticação e autorização adequadas
- Implemente registro (logging) para eventos de segurança
- Recomenda-se a realização de testes de segurança regulares

### Escopo

Esta política de segurança se aplica a:

- O código da aplicação principal
- As imagens oficiais do Docker
- A documentação que possa afetar a segurança
- As dependências que mantemos diretamente

### Fora do escopo

Os itens a seguir geralmente não são considerados vulnerabilidades de segurança:

- Problemas em dependências de terceiros (relate aos respectivos mantenedores)
- Ataques de engenharia social
- Problemas de segurança física
- Negação de serviço por esgotamento de recursos sem amplificação

### Porto seguro (Safe Harbor)

Apoiamos um porto seguro para pesquisadores de segurança que:

- Façam um esforço de boa-fé para evitar violações de privacidade e interrupção de serviço
- Interajam apenas com contas próprias ou com permissão explícita
- Não acessem nem modifiquem dados de outros usuários
- Relatem as vulnerabilidades prontamente
- Não divulguem publicamente as vulnerabilidades antes de serem resolvidas

### Contato

Para qualquer dúvida sobre esta política de segurança, entre em contato: security@pipeshub.com

---

*Esta política de segurança está sujeita a alterações. Verifique novamente regularmente para ver as atualizações.*
