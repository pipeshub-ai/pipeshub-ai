# Politica di sicurezza

<div align="center">

**Translations:** [English](../../../SECURITY.md) · [Français](../fr/SECURITY.md) · [Deutsch](../de/SECURITY.md) · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · [Español](../es/SECURITY.md) · [Português](../pt/SECURITY.md) · [Türkçe](../tr/SECURITY.md) · [Tiếng Việt](../vi/SECURITY.md) · **Italiano**

</div>

## Segnalare una vulnerabilità

Prendiamo sul serio le vulnerabilità di sicurezza e apprezziamo i tuoi sforzi nel divulgare in modo responsabile qualsiasi problema tu individui.

### Come segnalare

**Per favore, NON creare issue pubbliche su GitHub per le vulnerabilità di sicurezza.**

Segnala invece le vulnerabilità di sicurezza inviando un'e-mail a: **abhishek@pipeshub.com**

### Cosa includere

Quando segnali una vulnerabilità, includi:

- Una descrizione della vulnerabilità
- I passaggi per riprodurre il problema
- Una valutazione dell'impatto potenziale
- Una correzione suggerita (se disponibile)
- I tuoi recapiti per il follow-up

### Cosa aspettarsi

- **Risposta iniziale**: Confermeremo la ricezione della tua segnalazione entro 48 ore
- **Valutazione**: Forniremo una valutazione iniziale entro 5 giorni lavorativi
- **Aggiornamenti**: Invieremo aggiornamenti sui progressi ogni 7 giorni fino alla risoluzione
- **Risoluzione**: Puntiamo a risolvere le vulnerabilità critiche entro 30 giorni
- **Riconoscimento**: Riconosceremo il tuo contributo nei nostri avvisi di sicurezza (a meno che tu non preferisca restare anonimo)

### Cronologia della divulgazione

- **Giorno 0**: Vulnerabilità segnalata
- **Giorno 1-2**: Conferma iniziale
- **Giorno 1-5**: Triage iniziale e valutazione dell'impatto
- **Giorno 1-30**: Sviluppo e test della correzione
- **Giorno 30+**: Divulgazione pubblica dopo il rilascio della correzione, in genere entro 90 giorni dalla segnalazione iniziale.

### Valutazione delle vulnerabilità

Classifichiamo le vulnerabilità secondo i seguenti criteri:

- **Critica**: Minaccia immediata ai dati degli utenti o all'integrità del sistema
- **Alta**: Impatto significativo sulla sicurezza con ampia esposizione degli utenti
- **Media**: Impatto moderato sulla sicurezza con esposizione limitata
- **Bassa**: Problemi di sicurezza minori con impatto minimo

### Buone pratiche di sicurezza

Quando contribuisci a questo progetto:

- Mantieni le dipendenze aggiornate
- Segui pratiche di codifica sicura
- Convalida tutti gli input dell'utente
- Usa autenticazione e autorizzazione adeguate
- Implementa il logging per gli eventi di sicurezza
- Sono incoraggiati test di sicurezza regolari

### Ambito

Questa politica di sicurezza si applica a:

- Il codice dell'applicazione principale
- Le immagini Docker ufficiali
- La documentazione che potrebbe incidere sulla sicurezza
- Le dipendenze che manteniamo direttamente

### Fuori ambito

I seguenti elementi in genere non sono considerati vulnerabilità di sicurezza:

- Problemi nelle dipendenze di terze parti (segnalali ai rispettivi manutentori)
- Attacchi di ingegneria sociale
- Problemi di sicurezza fisica
- Negazione del servizio tramite esaurimento delle risorse senza amplificazione

### Safe Harbor

Sosteniamo un safe harbor per i ricercatori di sicurezza che:

- Si impegnano in buona fede a evitare violazioni della privacy e interruzioni del servizio
- Interagiscono solo con account di loro proprietà o con autorizzazione esplicita
- Non accedono né modificano i dati di altri utenti
- Segnalano tempestivamente le vulnerabilità
- Non divulgano pubblicamente le vulnerabilità prima che siano risolte

### Contatto

Per qualsiasi domanda su questa politica di sicurezza, contatta: security@pipeshub.com

---

*Questa politica di sicurezza è soggetta a modifiche. Ti invitiamo a consultarla regolarmente per gli aggiornamenti.*
