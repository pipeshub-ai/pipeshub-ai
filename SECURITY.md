# Security Policy

<div align="center">

**Translations:** [Français](docs/i18n/fr/SECURITY.md) · [Deutsch](docs/i18n/de/SECURITY.md) · [简体中文](docs/i18n/zh-CN/SECURITY.md) · [日本語](docs/i18n/ja/SECURITY.md) · [Русский](docs/i18n/ru/SECURITY.md) · [עברית](docs/i18n/he/SECURITY.md) · [한국어](docs/i18n/ko/SECURITY.md) · [Español](docs/i18n/es/SECURITY.md) · [Português](docs/i18n/pt/SECURITY.md) · [Türkçe](docs/i18n/tr/SECURITY.md) · [Tiếng Việt](docs/i18n/vi/SECURITY.md) · [Italiano](docs/i18n/it/SECURITY.md)

</div>

## Reporting a Vulnerability

We take security vulnerabilities seriously and appreciate your efforts to responsibly disclose any issues you find.

### How to Report

**Please DO NOT create public GitHub issues for security vulnerabilities.**

Instead, please report security vulnerabilities by emailing: **abhishek@pipeshub.com**

### What to Include

When reporting a vulnerability, please include:

- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact assessment
- Suggested fix (if available)
- Your contact information for follow-up

### What to Expect

- **Initial Response**: We will acknowledge receipt of your report within 48 hours
- **Assessment**: We will provide an initial assessment within 5 business days
- **Updates**: We will send progress updates every 7 days until resolution
- **Resolution**: We aim to resolve critical vulnerabilities within 30 days
- **Credit**: We will acknowledge your contribution in our security advisories (unless you prefer to remain anonymous)

### Disclosure Timeline

- **Day 0**: Vulnerability reported
- **Day 1-2**: Initial acknowledgment
- **Day 1-5**: Initial triage and impact assessment
- **Day 1-30**: Development and testing of fix
- **Day 30+**: Public disclosure after the fix is deployed, typically within 90 days of the initial report.

### Vulnerability Assessment

We classify vulnerabilities using the following criteria:

- **Critical**: Immediate threat to user data or system integrity
- **High**: Significant security impact with broad user exposure
- **Medium**: Moderate security impact with limited exposure
- **Low**: Minor security issues with minimal impact

### Security Best Practices

When contributing to this project:

- Keep dependencies up to date
- Follow secure coding practices
- Validate all user inputs
- Use proper authentication and authorization
- Implement logging for security events
- Regular security testing is encouraged

### Scope

This security policy applies to:

- The main application code
- Official Docker images
- Documentation that could affect security
- Dependencies we directly maintain

### Out of Scope

The following are generally not considered security vulnerabilities:

- Issues in third-party dependencies (please report to the respective maintainers)
- Social engineering attacks
- Physical security issues
- Denial of service through resource exhaustion without amplification

### Safe Harbor

We support safe harbor for security researchers who:

- Make a good faith effort to avoid privacy violations and service disruption
- Only interact with accounts you own or with explicit permission
- Do not access or modify other users' data
- Report vulnerabilities promptly
- Do not publicly disclose vulnerabilities before they are resolved

### Contact

For any questions about this security policy, please contact: security@pipeshub.com

---

*This security policy is subject to change. Please check back regularly for updates.*
