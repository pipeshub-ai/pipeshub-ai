# Güvenlik Politikası

<div align="center">

**Translations:** [English](../../../SECURITY.md) · [Français](../fr/SECURITY.md) · [Deutsch](../de/SECURITY.md) · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · [Español](../es/SECURITY.md) · [Português](../pt/SECURITY.md) · **Türkçe** · [Tiếng Việt](../vi/SECURITY.md) · [Italiano](../it/SECURITY.md)

</div>

## Bir Güvenlik Açığını Bildirme

Güvenlik açıklarını ciddiye alıyoruz ve bulduğunuz sorunları sorumlu bir şekilde açıklama çabalarınızı takdir ediyoruz.

### Nasıl Bildirilir

**Lütfen güvenlik açıkları için herkese açık GitHub issue'ları OLUŞTURMAYIN.**

Bunun yerine, güvenlik açıklarını şu adrese e-posta göndererek bildirin: **abhishek@pipeshub.com**

### Nelerin Dahil Edilmesi Gerekir

Bir güvenlik açığı bildirirken lütfen şunları ekleyin:

- Güvenlik açığının açıklaması
- Sorunu yeniden oluşturma adımları
- Olası etki değerlendirmesi
- Önerilen düzeltme (varsa)
- Takip için iletişim bilgileriniz

### Ne Beklemelisiniz

- **İlk Yanıt**: Raporunuzun alındığını 48 saat içinde onaylayacağız
- **Değerlendirme**: 5 iş günü içinde bir ilk değerlendirme sunacağız
- **Güncellemeler**: Çözüme kadar her 7 günde bir ilerleme güncellemeleri göndereceğiz
- **Çözüm**: Kritik güvenlik açıklarını 30 gün içinde çözmeyi hedefliyoruz
- **Teşekkür**: Katkınızı güvenlik danışma belgelerimizde anacağız (anonim kalmayı tercih etmediğiniz sürece)

### Açıklama Zaman Çizelgesi

- **0. Gün**: Güvenlik açığı bildirildi
- **1-2. Gün**: İlk onay
- **1-5. Gün**: İlk önceliklendirme ve etki değerlendirmesi
- **1-30. Gün**: Düzeltmenin geliştirilmesi ve test edilmesi
- **30+. Gün**: Düzeltme dağıtıldıktan sonra herkese açık açıklama, genellikle ilk bildirimden itibaren 90 gün içinde.

### Güvenlik Açığı Değerlendirmesi

Güvenlik açıklarını aşağıdaki ölçütlere göre sınıflandırıyoruz:

- **Kritik**: Kullanıcı verilerine veya sistem bütünlüğüne yönelik anlık tehdit
- **Yüksek**: Geniş kullanıcı kitlesini etkileyen önemli güvenlik etkisi
- **Orta**: Sınırlı etkiye sahip orta düzeyde güvenlik etkisi
- **Düşük**: Minimal etkili küçük güvenlik sorunları

### Güvenlik En İyi Uygulamaları

Bu projeye katkıda bulunurken:

- Bağımlılıkları güncel tutun
- Güvenli kodlama uygulamalarını izleyin
- Tüm kullanıcı girdilerini doğrulayın
- Uygun kimlik doğrulama ve yetkilendirme kullanın
- Güvenlik olayları için günlükleme uygulayın
- Düzenli güvenlik testleri teşvik edilir

### Kapsam

Bu güvenlik politikası şunlar için geçerlidir:

- Ana uygulama kodu
- Resmi Docker imajları
- Güvenliği etkileyebilecek dokümantasyon
- Doğrudan bizim baktığımız bağımlılıklar

### Kapsam Dışı

Aşağıdakiler genellikle güvenlik açığı olarak kabul edilmez:

- Üçüncü taraf bağımlılıklardaki sorunlar (lütfen ilgili bakım sorumlularına bildirin)
- Sosyal mühendislik saldırıları
- Fiziksel güvenlik sorunları
- Yükseltme (amplification) olmadan kaynak tüketimi yoluyla hizmet reddi

### Güvenli Liman (Safe Harbor)

Aşağıdaki koşulları sağlayan güvenlik araştırmacıları için güvenli limanı destekliyoruz:

- Gizlilik ihlallerinden ve hizmet kesintilerinden kaçınmak için iyi niyetli çaba gösterenler
- Yalnızca sahip oldukları hesaplarla veya açık izinle etkileşime girenler
- Diğer kullanıcıların verilerine erişmeyen veya bunları değiştirmeyenler
- Güvenlik açıklarını derhal bildirenler
- Çözülmeden önce güvenlik açıklarını herkese açık şekilde açıklamayanlar

### İletişim

Bu güvenlik politikasıyla ilgili her türlü soru için lütfen iletişime geçin: security@pipeshub.com

---

*Bu güvenlik politikası değişikliğe tabidir. Lütfen güncellemeler için düzenli olarak kontrol edin.*
