# Bezpečnostní zásady

<div align="center">

**Translations:** [English](../../../SECURITY.md) · [Français](../fr/SECURITY.md) · [Deutsch](../de/SECURITY.md) · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · [Español](../es/SECURITY.md) · [Português](../pt/SECURITY.md) · [Türkçe](../tr/SECURITY.md) · [Tiếng Việt](../vi/SECURITY.md) · [Italiano](../it/SECURITY.md) · **Čeština**

</div>

## Hlášení zranitelnosti

Bezpečnostní zranitelnosti bereme vážně a oceňujeme vaši snahu o zodpovědné zveřejnění všech problémů, které objevíte.

### Jak nahlásit

**Pro bezpečnostní zranitelnosti prosím NEVYTVÁŘEJTE veřejná GitHub issues.**

Místo toho prosím nahlaste bezpečnostní zranitelnosti e-mailem na adresu: **security@pipeshub.com**

### Co uvést

Při hlášení zranitelnosti prosím uveďte:

- Popis zranitelnosti
- Kroky k reprodukci problému
- Posouzení možného dopadu
- Navrhovanou opravu (pokud ji máte)
- Vaše kontaktní údaje pro další komunikaci

### Co můžete očekávat

- **První reakce**: Přijetí vašeho hlášení potvrdíme do 48 hodin
- **Posouzení**: Prvotní posouzení poskytneme do 5 pracovních dnů
- **Aktualizace**: Až do vyřešení budeme každých 7 dní zasílat informace o průběhu
- **Vyřešení**: Kritické zranitelnosti se snažíme vyřešit do 30 dnů
- **Uznání**: Váš přínos uvedeme v našich bezpečnostních upozorněních (pokud nechcete zůstat v anonymitě)

### Harmonogram zveřejnění

- **Den 0**: Zranitelnost nahlášena
- **Den 1–2**: Prvotní potvrzení
- **Den 1–5**: Prvotní třídění a posouzení dopadu
- **Den 1–30**: Vývoj a testování opravy
- **Den 30+**: Veřejné zveřejnění po nasazení opravy, obvykle do 90 dnů od prvotního hlášení.

### Posouzení zranitelností

Zranitelnosti klasifikujeme podle následujících kritérií:

- **Kritická**: Bezprostřední ohrožení uživatelských dat nebo integrity systému
- **Vysoká**: Významný bezpečnostní dopad s širokým vystavením uživatelů
- **Střední**: Mírný bezpečnostní dopad s omezeným vystavením
- **Nízká**: Drobné bezpečnostní problémy s minimálním dopadem

### Osvědčené bezpečnostní postupy

Při přispívání do tohoto projektu:

- Udržujte závislosti aktuální
- Dodržujte postupy bezpečného programování
- Validujte všechny uživatelské vstupy
- Používejte správnou autentizaci a autorizaci
- Implementujte logování bezpečnostních událostí
- Doporučujeme pravidelné bezpečnostní testování

### Rozsah platnosti

Tyto bezpečnostní zásady se vztahují na:

- Kód hlavní aplikace
- Oficiální Docker image
- Dokumentaci, která by mohla ovlivnit bezpečnost
- Závislosti, které přímo spravujeme

### Mimo rozsah platnosti

Následující případy se obecně nepovažují za bezpečnostní zranitelnosti:

- Problémy v závislostech třetích stran (nahlaste je prosím příslušným správcům)
- Útoky sociálním inženýrstvím
- Problémy fyzické bezpečnosti
- Odepření služby (denial of service) vyčerpáním zdrojů bez zesílení

### Safe harbor

Podporujeme zásadu safe harbor pro bezpečnostní výzkumníky, kteří:

- Vynakládají v dobré víře úsilí, aby se vyhnuli narušení soukromí a přerušení služby
- Pracují pouze s účty, které vlastní nebo k nimž mají výslovné svolení
- Nepřistupují k datům jiných uživatelů ani je neupravují
- Hlásí zranitelnosti bez zbytečného odkladu
- Nezveřejňují zranitelnosti dříve, než jsou vyřešeny

### Kontakt

S případnými dotazy k těmto bezpečnostním zásadám se prosím obracejte na: security@pipeshub.com

---

*Tyto bezpečnostní zásady se mohou změnit. Pravidelně prosím kontrolujte, zda nedošlo k aktualizaci.*
