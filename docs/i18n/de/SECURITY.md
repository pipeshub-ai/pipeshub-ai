# Sicherheitsrichtlinie

<div align="center">

**Translations:** [English](../../../SECURITY.md) · [Français](../fr/SECURITY.md) · **Deutsch** · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · [Español](../es/SECURITY.md) · [Português](../pt/SECURITY.md) · [Türkçe](../tr/SECURITY.md) · [Tiếng Việt](../vi/SECURITY.md) · [Italiano](../it/SECURITY.md)

</div>

## Eine Schwachstelle melden

Wir nehmen Sicherheitslücken ernst und schätzen deine Bemühungen, gefundene Probleme verantwortungsvoll offenzulegen.

### Wie man meldet

**Bitte erstelle KEINE öffentlichen GitHub-Issues für Sicherheitslücken.**

Melde Sicherheitslücken stattdessen per E-Mail an: **abhishek@pipeshub.com**

### Was anzugeben ist

Gib beim Melden einer Schwachstelle bitte Folgendes an:

- Beschreibung der Schwachstelle
- Schritte zur Reproduktion des Problems
- Einschätzung der möglichen Auswirkungen
- Vorgeschlagene Behebung (falls vorhanden)
- Deine Kontaktdaten für Rückfragen

### Was du erwarten kannst

- **Erste Antwort**: Wir bestätigen den Eingang deiner Meldung innerhalb von 48 Stunden
- **Bewertung**: Wir liefern innerhalb von 5 Werktagen eine erste Einschätzung
- **Updates**: Wir senden alle 7 Tage Fortschrittsupdates bis zur Behebung
- **Behebung**: Wir streben an, kritische Schwachstellen innerhalb von 30 Tagen zu beheben
- **Anerkennung**: Wir würdigen deinen Beitrag in unseren Sicherheitshinweisen (sofern du nicht anonym bleiben möchtest)

### Zeitplan der Offenlegung

- **Tag 0**: Schwachstelle gemeldet
- **Tag 1-2**: Erste Bestätigung
- **Tag 1-5**: Erste Triage und Folgenabschätzung
- **Tag 1-30**: Entwicklung und Test der Behebung
- **Tag 30+**: Öffentliche Offenlegung nach Bereitstellung der Behebung, in der Regel innerhalb von 90 Tagen nach der ursprünglichen Meldung.

### Bewertung von Schwachstellen

Wir klassifizieren Schwachstellen nach den folgenden Kriterien:

- **Kritisch**: Unmittelbare Bedrohung für Benutzerdaten oder die Systemintegrität
- **Hoch**: Erhebliche Sicherheitsauswirkung mit breiter Nutzerexposition
- **Mittel**: Moderate Sicherheitsauswirkung mit begrenzter Exposition
- **Niedrig**: Geringfügige Sicherheitsprobleme mit minimaler Auswirkung

### Bewährte Sicherheitspraktiken

Wenn du zu diesem Projekt beiträgst:

- Halte Abhängigkeiten aktuell
- Befolge sichere Programmierpraktiken
- Validiere alle Benutzereingaben
- Verwende ordnungsgemäße Authentifizierung und Autorisierung
- Implementiere Protokollierung für Sicherheitsereignisse
- Regelmäßige Sicherheitstests werden empfohlen

### Geltungsbereich

Diese Sicherheitsrichtlinie gilt für:

- Den Hauptanwendungscode
- Offizielle Docker-Images
- Dokumentation, die die Sicherheit beeinflussen könnte
- Abhängigkeiten, die wir direkt pflegen

### Außerhalb des Geltungsbereichs

Folgendes gilt im Allgemeinen nicht als Sicherheitslücke:

- Probleme in Abhängigkeiten von Drittanbietern (bitte an die jeweiligen Maintainer melden)
- Social-Engineering-Angriffe
- Probleme der physischen Sicherheit
- Denial of Service durch Ressourcenerschöpfung ohne Verstärkung

### Safe Harbor

Wir unterstützen Safe Harbor für Sicherheitsforschende, die:

- sich in gutem Glauben bemühen, Datenschutzverletzungen und Dienstunterbrechungen zu vermeiden
- nur mit eigenen Konten oder mit ausdrücklicher Erlaubnis interagieren
- nicht auf Daten anderer Benutzer zugreifen oder diese verändern
- Schwachstellen umgehend melden
- Schwachstellen nicht öffentlich offenlegen, bevor sie behoben sind

### Kontakt

Bei Fragen zu dieser Sicherheitsrichtlinie kontaktiere bitte: security@pipeshub.com

---

*Diese Sicherheitsrichtlinie kann sich ändern. Bitte schaue regelmäßig nach Aktualisierungen.*
