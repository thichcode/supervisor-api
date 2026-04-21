# Roadmap: User Reply + Feedback Loop

> Tài liệu này mô tả hướng triển khai sau cho phần nghiệp vụ trả lời user và nhận phản hồi user.
> Mục tiêu là làm rõ luồng end-to-end trước khi chạm vào code.

## 1) Bối cảnh

Hiện tại hệ thống supervisor-api đã có các mảnh sau:
- nhận message từ Telegram / webhook / chat API
- phân tích intent, KB, approval, confidence
- đẩy một số kết quả sang Power Automate
- có dashboard theo dõi vận hành

Tuy nhiên phần nghiệp vụ vẫn còn thiếu 2 điểm quan trọng:
- chưa trả lời user đủ ổn định theo end-to-end flow
- chưa thu được phản hồi user một cách có cấu trúc để học tiếp

Tài liệu này không thay thế code hiện tại. Nó là bản kế hoạch để triển khai sau khi logic nghiệp vụ đã chốt.

## 2) Mục tiêu

1. Trả lời user đúng kênh, đúng ngữ cảnh, đúng trạng thái.
2. Thu phản hồi user sau khi bot trả lời.
3. Ghi nhận phản hồi thành dữ liệu có thể audit, replay, và học lại.
4. Giữ confidence và routing conservative, không auto-optimistic.
5. Tránh tự động hóa quá sớm khi chưa có đủ tín hiệu.

## 3) Vấn đề hiện tại cần giải quyết

### 3.1 Chưa trả lời user ổn định
Một số case có thể:
- vào được supervisor nhưng chưa đi tới bước reply cuối
- có approval nhưng chưa post lại kết quả về user
- response route đúng nhưng delivery path chưa khớp với channel thực tế

### 3.2 Chưa nhận feedback user rõ ràng
Hiện cần có luồng feedback chuẩn để ghi nhận:
- approve / reject
- edit / change request
- thumbs up / thumbs down
- user bổ sung ngữ cảnh sau khi bot trả lời
- user im lặng nhưng message tiếp theo cho thấy output trước đó chưa đủ

### 3.3 Thiếu vòng lặp đóng
Hệ thống cần biết:
- bot đã trả lời chưa
- user có chấp nhận không
- phản hồi đó có tác động gì tới confidence / profile / routing lần sau

## 4) Target behavior

Luồng mong muốn:

1. User gửi message.
2. Supervisor phân tích ngữ cảnh, KB, confidence, risk.
3. Hệ thống quyết định:
   - reply ngay
   - cần approval trước khi reply
   - yêu cầu hỏi lại để lấy thêm ngữ cảnh
   - skip nếu confidence quá thấp
4. Khi reply được gửi, hệ thống tạo một record liên kết với request gốc.
5. User phản hồi trên cùng thread/case/conversation.
6. Feedback được lưu thành event riêng, gắn với response đã gửi.
7. Learning layer chỉ dùng feedback đã chuẩn hóa và có audit.

## 5) Nguyên tắc thiết kế

- Đừng trộn response delivery với feedback learning.
- Đừng để một feedback đơn lẻ làm đổi hành vi quá mạnh.
- Đừng auto-learn nếu chưa có đủ tín hiệu và đủ ổn định theo segment.
- Mỗi response phải trace được từ input → decision → delivery → feedback.
- Nếu không chắc, ưu tiên an toàn hơn là “thông minh giả”.

## 6) Dữ liệu cần có

### 6.1 Response record
Mỗi response nên lưu tối thiểu:
- request_id
- conversation_id / thread_id
- user_id
- channel
- response_text
- confidence
- kb_hit
- route quyết định
- approval_status nếu có
- delivered_at
- delivery_status
- correlation_id

### 6.2 Feedback record
Mỗi feedback nên lưu tối thiểu:
- feedback_id
- request_id / response_id
- feedback_type
- signal_strength
- user_id
- channel
- text feedback nếu có
- timestamp
- metadata

### 6.3 Conversation state
Nên giữ trạng thái nhẹ để biết:
- đang chờ reply
- đang chờ approval
- đã reply xong
- đang chờ feedback
- cần hỏi lại
- đang mở loop nào

## 7) Proposed flow

### Flow A: Trả lời trực tiếp
- confidence đủ thấp để không auto-send, nhưng đủ cao để trả lời.
- Supervisor tạo response.
- Chat service gửi reply về đúng channel.
- Response được log là delivered.
- Conversation chuyển sang trạng thái chờ feedback.

### Flow B: Cần approval
- Supervisor tạo đề xuất trả lời.
- Approval channel nhận card/message.
- Người duyệt approve/reject.
- Nếu approve: reply mới được gửi cho user.
- Nếu reject: lưu feedback xấu và không gửi.

### Flow C: Cần hỏi thêm
- Nếu input thiếu ngữ cảnh, bot không cố trả lời ngay.
- Bot hỏi lại một câu ngắn, rõ, cụ thể.
- Câu hỏi follow-up cũng phải được log như một response.

### Flow D: Nhận feedback sau reply
- User trả lời tiếp trong cùng conversation.
- Hệ thống gắn message đó với response gần nhất.
- Nếu feedback là sửa sai rõ ràng, đánh dấu hard signal.
- Nếu feedback là đồng ý, đánh dấu soft signal.

## 8) Feedback taxonomy gợi ý

### Hard signals
- reject
- correction
- escalation
- reopen
- user nói bot trả lời sai
- user yêu cầu làm lại

### Soft signals
- approve
- acknowledge
- thanks
- user tiếp tục hỏi cùng chủ đề
- không có phàn nàn sau reply

### Rule
- Hard signal phải nặng hơn soft signal.
- Không dùng một lời khen để xoá một lỗi nặng.
- Không dùng một feedback nhỏ để tăng confidence quá mạnh.

## 9) Rollout theo pha

### Pha 1: Observe only
- chỉ log request / response / feedback
- chưa dùng feedback để thay đổi routing
- mục tiêu là nhìn thấy luồng end-to-end

### Pha 2: Reply stable
- chốt các path trả lời user
- đảm bảo mọi response đều có delivery status
- xử lý rõ ràng reply ngay / approval / hỏi lại

### Pha 3: Feedback capture
- chuẩn hóa feedback event
- map feedback vào response gốc
- hiển thị trong dashboard

### Pha 4: Controlled learning
- dùng feedback đã chuẩn hóa để điều chỉnh confidence
- chỉ áp dụng cho segment ổn định
- có rollback nếu quality giảm

## 10) Immediate deployment plan

Mục tiêu phần này là làm được ngay trong codebase hiện tại, không phải ý tưởng xa.

### Bước 1: Nối Grafana vào metrics có sẵn
Hiện code đã có Prometheus metrics endpoint qua `/metrics`, nên phần đầu tiên là dùng chính endpoint này làm nguồn cho Grafana.

#### Nguồn dữ liệu hiện có
- `src/core/metrics.py`
  - `supervisor_requests_total`
  - `supervisor_request_duration_seconds`
  - `supervisor_active_requests`
  - `supervisor_llm_requests_total`
  - `supervisor_llm_duration_seconds`
  - `supervisor_memory_operations_total`
  - `supervisor_decisions_total`
  - `supervisor_errors_total`
  - `supervisor_redis_errors_total`
  - `supervisor_external_memory_operations_total`
- `src/api/routers/health.py`
  - `GET /metrics`
- `src/api/routers/monitoring.py`
  - `GET /metrics/dashboard`
  - `GET /metrics/dashboard/html`
  - `GET /metrics/dashboard/boss-report`

#### Grafana panels nên dựng ngay
- Request rate và error rate
- Latency p50 / p95 / p99
- LLM request volume + duration
- Decision mix: approve / skip / send / clarify
- Redis errors + external memory operations
- Top endpoints theo request volume

#### Việc cần làm tiếp theo trong code
- expose thêm metric cho KB search/hit/miss
- expose metric cho clarification / fallback / delivery fail
- expose metric cho approval created / approved / rejected

### Bước 2: Tăng KB hit bằng cách instrument đúng điểm search
Trước khi tối ưu retrieval, phải đo được bot đang miss ở đâu.

#### File sẽ chạm
- `src/knowledge/service.py`
- `src/core/supervisor.py`
- `src/services/chat_service.py`
- `src/core/intent_classifier.py`
- `src/api/routers/monitoring.py`
- `src/core/metrics.py`
- `tests/test_core.py`
- `tests/test_router_smoke.py`

#### Metric cần thêm ngay
- `kb_search_total`
- `kb_hit_total`
- `kb_miss_total`
- `kb_rerank_total`
- `kb_clarification_total`
- `kb_fallback_total`

#### Cách dùng metric
- Nếu `kb_miss_total` cao ở một intent cụ thể -> sửa query normalization hoặc KB content
- Nếu `kb_hit_total` thấp nhưng intent rõ -> tăng synonym / alias / rerank
- Nếu `kb_clarification_total` cao -> câu hỏi ban đầu đang mơ hồ, nên hỏi lại sớm hơn

### Bước 3: Tăng chất lượng trả lời bằng 5 thay đổi nhỏ nhưng hiệu quả

#### 3.1 Normalize query trước khi search
- chuẩn hóa tiếng Việt có dấu / không dấu
- map synonym viết tắt
- cắt noise từ message dài

#### 3.2 Hybrid retrieval
- keyword search + semantic search
- lấy top-k rồi rerank
- không tự tin khi evidence yếu

#### 3.3 Context rerank
- rerank theo user role, service, chat_scope, thread history
- ưu tiên KB gần ngữ cảnh hiện tại

#### 3.4 Clarify sớm khi câu hỏi mơ hồ
- nếu thiếu keyword quan trọng thì hỏi lại 1 câu ngắn
- không search bừa rồi trả lời đoán

#### 3.5 Feedback loop nhẹ
- approve / reject / correction / thanks
- hard signal nặng hơn soft signal
- chỉ dùng feedback để điều chỉnh sau khi đã có đủ mẫu

## 11) KB hit uplift roadmap

Nếu mục tiêu là tăng hit KB hoặc trả lời đúng yêu cầu user hơn, nên ưu tiên theo thứ tự sau:

### 11.1 Chuẩn hóa câu hỏi trước khi search
- normalize tiếng Việt, viết tắt, synonym
- tách intent rõ: hỏi thông tin, hỏi quy trình, hỏi lỗi, hỏi trạng thái
- loại bỏ nhiễu từ message dài / multi-line
- nếu câu quá mơ hồ, hỏi lại thay vì search bừa

### 11.2 Dùng hybrid retrieval
- kết hợp keyword search + semantic search
- ưu tiên hybrid hơn chỉ vector hoặc chỉ BM25
- lấy top-k đủ rộng rồi rerank
- không trả lời nếu evidence yếu quá

### 11.3 Rerank theo ngữ cảnh
- rerank theo user role, team, service, chat_scope, thread history
- ưu tiên KB gần ngữ cảnh hiện tại
- hạ ưu tiên kết quả cũ hoặc quá chung chung

### 11.4 Tách câu hỏi phức tạp
- nếu user hỏi nhiều ý, tách thành sub-question
- trả lời từng ý có evidence riêng
- nếu thiếu 1 ý quan trọng thì hỏi lại thay vì đoán

### 11.5 Dùng clarification khi confidence thấp
- thay vì trả lời sai, hỏi một câu ngắn để lấy thêm ngữ cảnh
- ví dụ: "Bạn đang hỏi về backup job, restore job hay backup compliance?"
- mục tiêu là tăng KB hit thật, không phải tăng confidence giả

### 11.6 Tối ưu KB content
- chuẩn hóa title, tags, summary
- thêm synonym/alias cho thuật ngữ nghiệp vụ
- chia tài liệu quá dài thành các đoạn nhỏ có chủ đề rõ
- thêm ví dụ câu hỏi người dùng thường dùng

### 11.7 Quan sát bằng dashboard
- xem KB hit rate theo intent/service/channel
- xem query nào miss nhiều nhất
- xem câu hỏi nào hay bị fallback dù KB có sẵn
- dùng Grafana để xác định nơi cần sửa KB trước

## 12) Files cần chạm ngay nếu muốn triển khai

- `src/core/metrics.py` — thêm metric cho KB hit/miss/fallback/delivery
- `src/knowledge/service.py` — instrument search, hit, miss, rerank
- `src/core/supervisor.py` — instrument decision + clarification + fallback
- `src/services/chat_service.py` — instrument delivery success/failure và feedback linkage
- `src/api/routers/monitoring.py` — hiển thị dashboard KPIs mới
- `src/api/routers/health.py` — giữ `/metrics` làm scrape target cho Prometheus
- `tests/test_core.py` — test query clarification, KB miss/hit routing
- `tests/test_router_smoke.py` — test dashboard / metric endpoint

## 13) Acceptance criteria trước khi đem đi deploy

- Mỗi request có thể trace tới response đã gửi.
- Mỗi response có thể trace tới feedback sau đó.
- User nhận được reply đúng channel.
- Approval flow không làm mất response.
- Feedback được ghi nhận đúng thread/conversation.
- Dashboard nhìn được tỷ lệ reply, approval, skip, feedback.
- Không có confidence inflation từ các đường fallback.
- KB hit rate phải nhìn được theo intent/service/channel.
- Grafana phải cho thấy bot đang miss ở đâu, không chỉ tổng quan đẹp.

## 14) Guardrails

- Không deploy nếu response path chưa chạy end-to-end.
- Không deploy nếu feedback chỉ ghi log mà không link được response.
- Không deploy nếu approval có thể tạo nhưng không gửi được reply cuối.
- Không deploy nếu chưa test case “user reply → system reply → user feedback”.
- Không tăng confidence trước khi KB hit/miss được đo rõ ràng.

## 15) Kết luận

Ưu tiên sắp tới không phải thêm nhiều intelligence mới, mà là chốt 3 thứ:
- bot có trả lời được user không
- hệ thống có nhận được phản hồi user không
- mọi thứ có trace được qua request/response/feedback không

Khi 3 thứ này ổn, mới tính chuyện học sâu hơn hoặc tự động hóa mạnh hơn.
