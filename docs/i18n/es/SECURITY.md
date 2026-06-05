# Política de seguridad

<div align="center">

**Translations:** [English](../../../SECURITY.md) · [Français](../fr/SECURITY.md) · [Deutsch](../de/SECURITY.md) · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · **Español** · [Português](../pt/SECURITY.md) · [Türkçe](../tr/SECURITY.md) · [Tiếng Việt](../vi/SECURITY.md) · [Italiano](../it/SECURITY.md)

</div>

## Reportar una vulnerabilidad

Nos tomamos en serio las vulnerabilidades de seguridad y agradecemos tus esfuerzos por divulgar de forma responsable cualquier problema que encuentres.

### Cómo reportar

**Por favor, NO crees issues públicos de GitHub para vulnerabilidades de seguridad.**

En su lugar, reporta las vulnerabilidades de seguridad enviando un correo a: **abhishek@pipeshub.com**

### Qué incluir

Al reportar una vulnerabilidad, incluye lo siguiente:

- Descripción de la vulnerabilidad
- Pasos para reproducir el problema
- Evaluación del impacto potencial
- Solución sugerida (si está disponible)
- Tu información de contacto para el seguimiento

### Qué esperar

- **Respuesta inicial**: Confirmaremos la recepción de tu reporte en un plazo de 48 horas
- **Evaluación**: Proporcionaremos una evaluación inicial en un plazo de 5 días hábiles
- **Actualizaciones**: Enviaremos actualizaciones de progreso cada 7 días hasta la resolución
- **Resolución**: Procuramos resolver las vulnerabilidades críticas en un plazo de 30 días
- **Reconocimiento**: Reconoceremos tu contribución en nuestros avisos de seguridad (a menos que prefieras permanecer anónimo)

### Cronología de divulgación

- **Día 0**: Se reporta la vulnerabilidad
- **Día 1-2**: Confirmación inicial
- **Día 1-5**: Clasificación inicial y evaluación del impacto
- **Día 1-30**: Desarrollo y prueba de la solución
- **Día 30+**: Divulgación pública tras desplegar la solución, normalmente dentro de los 90 días desde el reporte inicial.

### Evaluación de vulnerabilidades

Clasificamos las vulnerabilidades según los siguientes criterios:

- **Crítica**: Amenaza inmediata a los datos del usuario o a la integridad del sistema
- **Alta**: Impacto de seguridad significativo con amplia exposición de usuarios
- **Media**: Impacto de seguridad moderado con exposición limitada
- **Baja**: Problemas de seguridad menores con impacto mínimo

### Buenas prácticas de seguridad

Al contribuir a este proyecto:

- Mantén las dependencias actualizadas
- Sigue prácticas de codificación segura
- Valida todas las entradas del usuario
- Usa autenticación y autorización adecuadas
- Implementa registro (logging) para eventos de seguridad
- Se recomienda realizar pruebas de seguridad periódicas

### Alcance

Esta política de seguridad se aplica a:

- El código de la aplicación principal
- Las imágenes oficiales de Docker
- La documentación que pueda afectar a la seguridad
- Las dependencias que mantenemos directamente

### Fuera del alcance

Lo siguiente, por lo general, no se considera una vulnerabilidad de seguridad:

- Problemas en dependencias de terceros (repórtalos a los mantenedores correspondientes)
- Ataques de ingeniería social
- Problemas de seguridad física
- Denegación de servicio mediante agotamiento de recursos sin amplificación

### Puerto seguro (Safe Harbor)

Apoyamos un puerto seguro para los investigadores de seguridad que:

- Hagan un esfuerzo de buena fe para evitar violaciones de privacidad e interrupciones del servicio
- Solo interactúen con cuentas propias o con permiso explícito
- No accedan ni modifiquen los datos de otros usuarios
- Reporten las vulnerabilidades con prontitud
- No divulguen públicamente las vulnerabilidades antes de que se resuelvan

### Contacto

Para cualquier pregunta sobre esta política de seguridad, contacta a: security@pipeshub.com

---

*Esta política de seguridad está sujeta a cambios. Vuelve a consultarla periódicamente para ver las actualizaciones.*
