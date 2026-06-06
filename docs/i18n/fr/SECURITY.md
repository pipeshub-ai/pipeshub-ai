# Politique de sécurité

<div align="center">

**Translations:** [English](../../../SECURITY.md) · **Français** · [Deutsch](../de/SECURITY.md) · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · [Español](../es/SECURITY.md) · [Português](../pt/SECURITY.md) · [Türkçe](../tr/SECURITY.md) · [Tiếng Việt](../vi/SECURITY.md) · [Italiano](../it/SECURITY.md)

</div>

## Signaler une vulnérabilité

Nous prenons les vulnérabilités de sécurité au sérieux et apprécions vos efforts pour divulguer de manière responsable tout problème que vous découvrez.

### Comment signaler

**Veuillez NE PAS créer d'issues GitHub publiques pour les vulnérabilités de sécurité.**

Signalez plutôt les vulnérabilités de sécurité par e-mail à : **abhishek@pipeshub.com**

### Ce qu'il faut inclure

Lorsque vous signalez une vulnérabilité, veuillez inclure :

- Une description de la vulnérabilité
- Les étapes pour reproduire le problème
- Une évaluation de l'impact potentiel
- Une correction suggérée (si disponible)
- Vos coordonnées pour le suivi

### À quoi s'attendre

- **Réponse initiale** : Nous accuserons réception de votre signalement sous 48 heures
- **Évaluation** : Nous fournirons une évaluation initiale sous 5 jours ouvrés
- **Mises à jour** : Nous enverrons des mises à jour d'avancement tous les 7 jours jusqu'à la résolution
- **Résolution** : Nous visons à résoudre les vulnérabilités critiques sous 30 jours
- **Crédit** : Nous reconnaîtrons votre contribution dans nos avis de sécurité (sauf si vous préférez rester anonyme)

### Calendrier de divulgation

- **Jour 0** : Vulnérabilité signalée
- **Jour 1-2** : Accusé de réception initial
- **Jour 1-5** : Triage initial et évaluation de l'impact
- **Jour 1-30** : Développement et test du correctif
- **Jour 30+** : Divulgation publique après le déploiement du correctif, généralement dans les 90 jours suivant le signalement initial.

### Évaluation des vulnérabilités

Nous classons les vulnérabilités selon les critères suivants :

- **Critique** : Menace immédiate pour les données des utilisateurs ou l'intégrité du système
- **Élevée** : Impact de sécurité important avec une large exposition des utilisateurs
- **Moyenne** : Impact de sécurité modéré avec une exposition limitée
- **Faible** : Problèmes de sécurité mineurs avec un impact minimal

### Bonnes pratiques de sécurité

Lorsque vous contribuez à ce projet :

- Maintenez les dépendances à jour
- Suivez des pratiques de codage sécurisées
- Validez toutes les entrées utilisateur
- Utilisez une authentification et une autorisation appropriées
- Mettez en place une journalisation des événements de sécurité
- Des tests de sécurité réguliers sont encouragés

### Périmètre

Cette politique de sécurité s'applique à :

- Le code de l'application principale
- Les images Docker officielles
- La documentation susceptible d'affecter la sécurité
- Les dépendances que nous maintenons directement

### Hors périmètre

Les éléments suivants ne sont généralement pas considérés comme des vulnérabilités de sécurité :

- Les problèmes dans des dépendances tierces (signalez-les aux mainteneurs concernés)
- Les attaques d'ingénierie sociale
- Les problèmes de sécurité physique
- Le déni de service par épuisement des ressources sans amplification

### Sphère de sécurité (Safe Harbor)

Nous soutenons une sphère de sécurité pour les chercheurs en sécurité qui :

- Font un effort de bonne foi pour éviter les atteintes à la vie privée et les interruptions de service
- N'interagissent qu'avec des comptes leur appartenant ou avec une autorisation explicite
- N'accèdent pas aux données d'autres utilisateurs et ne les modifient pas
- Signalent rapidement les vulnérabilités
- Ne divulguent pas publiquement les vulnérabilités avant leur résolution

### Contact

Pour toute question concernant cette politique de sécurité, contactez : security@pipeshub.com

---

*Cette politique de sécurité est susceptible d'être modifiée. Veuillez la consulter régulièrement pour les mises à jour.*
