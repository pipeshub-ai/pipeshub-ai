# Chính sách bảo mật

<div align="center">

**Translations:** [English](../../../SECURITY.md) · [Français](../fr/SECURITY.md) · [Deutsch](../de/SECURITY.md) · [简体中文](../zh-CN/SECURITY.md) · [日本語](../ja/SECURITY.md) · [Русский](../ru/SECURITY.md) · [עברית](../he/SECURITY.md) · [한국어](../ko/SECURITY.md) · [Español](../es/SECURITY.md) · [Português](../pt/SECURITY.md) · [Türkçe](../tr/SECURITY.md) · **Tiếng Việt** · [Italiano](../it/SECURITY.md)

</div>

## Báo cáo lỗ hổng

Chúng tôi xem trọng các lỗ hổng bảo mật và đánh giá cao những nỗ lực của bạn trong việc tiết lộ có trách nhiệm bất kỳ vấn đề nào bạn phát hiện.

### Cách báo cáo

**Vui lòng KHÔNG tạo issue công khai trên GitHub cho các lỗ hổng bảo mật.**

Thay vào đó, vui lòng báo cáo các lỗ hổng bảo mật bằng cách gửi email đến: **abhishek@pipeshub.com**

### Những gì cần đưa vào

Khi báo cáo một lỗ hổng, vui lòng đưa vào:

- Mô tả lỗ hổng
- Các bước để tái hiện vấn đề
- Đánh giá tác động tiềm tàng
- Đề xuất cách khắc phục (nếu có)
- Thông tin liên hệ của bạn để theo dõi

### Những gì có thể mong đợi

- **Phản hồi ban đầu**: Chúng tôi sẽ xác nhận đã nhận báo cáo của bạn trong vòng 48 giờ
- **Đánh giá**: Chúng tôi sẽ cung cấp đánh giá ban đầu trong vòng 5 ngày làm việc
- **Cập nhật**: Chúng tôi sẽ gửi cập nhật tiến độ mỗi 7 ngày cho đến khi giải quyết xong
- **Giải quyết**: Chúng tôi hướng đến giải quyết các lỗ hổng nghiêm trọng trong vòng 30 ngày
- **Ghi nhận**: Chúng tôi sẽ ghi nhận đóng góp của bạn trong các thông báo bảo mật (trừ khi bạn muốn ẩn danh)

### Lịch trình tiết lộ

- **Ngày 0**: Lỗ hổng được báo cáo
- **Ngày 1-2**: Xác nhận ban đầu
- **Ngày 1-5**: Phân loại ban đầu và đánh giá tác động
- **Ngày 1-30**: Phát triển và kiểm thử bản vá
- **Ngày 30+**: Tiết lộ công khai sau khi bản vá được triển khai, thường trong vòng 90 ngày kể từ báo cáo ban đầu.

### Đánh giá lỗ hổng

Chúng tôi phân loại lỗ hổng theo các tiêu chí sau:

- **Nghiêm trọng (Critical)**: Mối đe dọa tức thời đối với dữ liệu người dùng hoặc tính toàn vẹn của hệ thống
- **Cao (High)**: Tác động bảo mật đáng kể với phạm vi ảnh hưởng rộng đến người dùng
- **Trung bình (Medium)**: Tác động bảo mật vừa phải với phạm vi ảnh hưởng hạn chế
- **Thấp (Low)**: Các vấn đề bảo mật nhỏ với tác động tối thiểu

### Thực hành bảo mật tốt nhất

Khi đóng góp cho dự án này:

- Cập nhật các phụ thuộc lên phiên bản mới nhất
- Tuân theo các thực hành lập trình an toàn
- Xác thực mọi đầu vào của người dùng
- Sử dụng xác thực và phân quyền phù hợp
- Triển khai ghi nhật ký (logging) cho các sự kiện bảo mật
- Khuyến khích kiểm thử bảo mật định kỳ

### Phạm vi

Chính sách bảo mật này áp dụng cho:

- Mã ứng dụng chính
- Các image Docker chính thức
- Tài liệu có thể ảnh hưởng đến bảo mật
- Các phụ thuộc do chúng tôi trực tiếp duy trì

### Ngoài phạm vi

Những điều sau đây thường không được coi là lỗ hổng bảo mật:

- Vấn đề trong các phụ thuộc của bên thứ ba (vui lòng báo cáo cho người bảo trì tương ứng)
- Tấn công kỹ thuật xã hội (social engineering)
- Vấn đề bảo mật vật lý
- Từ chối dịch vụ thông qua làm cạn kiệt tài nguyên mà không có khuếch đại

### Vùng an toàn (Safe Harbor)

Chúng tôi hỗ trợ vùng an toàn cho các nhà nghiên cứu bảo mật nếu họ:

- Nỗ lực thiện chí để tránh vi phạm quyền riêng tư và gián đoạn dịch vụ
- Chỉ tương tác với các tài khoản của chính bạn hoặc được phép rõ ràng
- Không truy cập hoặc sửa đổi dữ liệu của người dùng khác
- Báo cáo lỗ hổng kịp thời
- Không tiết lộ công khai lỗ hổng trước khi chúng được giải quyết

### Liên hệ

Nếu có bất kỳ câu hỏi nào về chính sách bảo mật này, vui lòng liên hệ: security@pipeshub.com

---

*Chính sách bảo mật này có thể thay đổi. Vui lòng kiểm tra lại thường xuyên để cập nhật.*
