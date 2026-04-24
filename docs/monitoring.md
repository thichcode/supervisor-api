# Monitoring loop cho product theo dõi đa chiều

Tài liệu này chốt lại insight thực tế để chạy trong product: hệ thống không nên chỉ “học từ feedback”, mà phải theo dõi đa tín hiệu, có lớp kiểm soát, và đủ quan sát để biết nó đang đúng ở đâu, sai ở đâu, và khi nào phải dừng tự động hóa.

---

## 1) Ý tưởng cốt lõi

Một product theo dõi đa chiều nên vận hành theo 3 lớp riêng biệt:

1. Observe
   - Ghi nhận sự kiện và ngữ cảnh.
   - Không suy diễn quá sớm.

2. Learn
   - Tổng hợp tín hiệu thành confidence, trend, profile, và cảnh báo.
   - Có thể replay lịch sử nhưng phải idempotent và có audit.

3. Act
   - Dùng kết quả để gợi ý, tự động hóa, hoặc escalate.
   - Không nên để một feedback đơn lẻ tạo ra hành động mạnh ngay.

Nếu trộn 3 lớp này, hệ thống sẽ dễ “tự tin sai”.

---

## 2) Sơ đồ luồng sản phẩm

```text
Nguồn dữ liệu
  ├─ Ticket / Incident / Request
  ├─ Feedback user
  ├─ Hành vi sửa tay sau khi AI trả lời
  ├─ SLA / latency / escalation / reopen
  └─ Audit / exception / error logs
            │
            ▼
     [Observe Layer]
            │
            ▼
   Normalize + Enrich + Tag context
            │
            ▼
      [Learning Layer]
   ├─ Bayesian confidence
   ├─ Profile theo tenant/team/service
   ├─ Trend + decay + drift
   └─ Replay / re-score
            │
            ▼
        [Act Layer]
   ├─ Suggest
   ├─ Auto-approve / auto-route
   ├─ Escalate
   └─ Freeze automation khi rủi ro cao
```

---

## 3) Không chỉ đo “đúng/sai”

Một answer có thể:
- đúng nội dung nhưng sai ngữ cảnh,
- đúng ngữ cảnh nhưng chậm,
- được approve nhưng sau đó user sửa rất nhiều,
- ổn ở incident nhưng tệ ở request.

Vì vậy cần track theo nhiều chiều:

- chất lượng nội dung
- độ chính xác theo ngữ cảnh
- tốc độ phản hồi
- tỉ lệ chấp nhận
- mức sửa tay sau output AI
- tỉ lệ reopen / escalation
- độ ổn định theo tenant / team / service
- xu hướng theo thời gian

Kết luận: feedback là một tín hiệu, không phải sự thật tuyệt đối.

---

## 4) Phân loại tín hiệu

### Hard signal
Tín hiệu mạnh, nên ưu tiên cao:
- reject
- escalation
- SLA breach
- reopen ticket
- user chỉnh lại output nhiều lần
- validation fail

### Soft signal
Tín hiệu yếu, chỉ dùng để tham khảo:
- like / approve
- dwell time
- không có phản hồi tiêu cực
- user click tiếp tục

### Quy tắc
- Hard signal phải ảnh hưởng mạnh hơn soft signal.
- Một soft signal tốt không được xóa sạch hard signal xấu.
- Nên có decay để dữ liệu cũ không lấn át dữ liệu mới.

---

## 5) Phải tách profile theo ngữ cảnh

Không nên học chung toàn hệ thống.

Nên tách theo:
- tenant
- team
- service
- loại ticket
- priority / severity
- ngôn ngữ / style
- kênh nhận yêu cầu

Ví dụ:
- cùng một câu trả lời có thể tốt cho monitoring, nhưng không tốt cho backup restore;
- cùng một tone có thể hợp với internal chat, nhưng không hợp với ticket chính thức.

Nếu gộp tất cả vào một model profile, confidence sẽ “đẹp giả”.

---

## 6) Learning loop thực dụng

### Bước 1: Collect
Ghi lại event với đủ ngữ cảnh tối thiểu:
- event_id
- timestamp
- source
- tenant/team/service
- action type
- feedback type
- outcome
- related ticket / request / conversation

### Bước 2: Normalize
Chuẩn hóa dữ liệu:
- dedupe
- map về taxonomy chung
- gán priority
- enrich context

### Bước 3: Score
Tính các chỉ số:
- confidence
- risk
- drift
- trend
- confidence by segment

### Bước 4: Replay
Chỉ replay khi:
- event idempotent
- có version của rule/model
- có audit trail
- có thể rollback

### Bước 5: Act
Chỉ bật automation khi:
- confidence đủ cao,
- drift thấp,
- hard signals không xấu,
- segment đó đã ổn định.

---

## 7) Event schema gợi ý

```json
{
  "event_id": "evt_123",
  "timestamp": "2026-04-15T22:51:00Z",
  "source": "teams",
  "tenant": "acme",
  "team": "noc",
  "service": "backup",
  "ticket_type": "incident",
  "priority": "high",
  "signal_type": "reject",
  "signal_strength": "hard",
  "outcome": "needs_revision",
  "confidence_before": 0.72,
  "confidence_after": 0.61,
  "correlation_id": "corr_abc",
  "version": "v1",
  "metadata": {
    "language": "vi",
    "channel": "telegram"
  }
}
```

Mục tiêu không phải lưu thật nhiều, mà là lưu đúng phần đủ để replay và giải thích.

---

## 8) Dashboard nên trả lời được gì

Dashboard tốt phải trả lời nhanh các câu hỏi sau:

- Hôm nay có bao nhiêu feedback?
- Bao nhiêu event đã replay?
- Segment nào đang lệch confidence?
- Tenant/team nào có tỉ lệ reject cao?
- Drift xuất hiện ở đâu?
- Rule nào tạo nhiều false positive?
- Tín hiệu nào đang làm confidence tăng/giảm?
- Có nên tắt auto mode ở segment nào không?

Nếu không nhìn được các câu này, hệ thống học sẽ thành “tự ảo giác”.

---

## 9) Guardrails bắt buộc

1. Không để một feedback đơn lẻ quyết định hành động lớn.
2. Luôn giữ audit log cho replay và cập nhật confidence.
3. Có decay để dữ liệu cũ giảm trọng số theo thời gian.
4. Phân tách suggest mode và automate mode.
5. Khi hard signals xấu, ưu tiên an toàn hơn tối ưu.
6. Có rollback khi rule hoặc model mới làm lệch hành vi.
7. Chỉ auto-learn ở use case đã ổn định.

---

## 10) Chiến lược rollout trong product

### Giai đoạn 1: Observe only
- Chỉ ghi nhận và dashboard.
- Không tự động hóa.

### Giai đoạn 2: Suggest mode
- AI đề xuất, con ngườii duyệt.
- Theo dõi hard/soft signals.

### Giai đoạn 3: Limited automation
- Chỉ bật ở segment nhỏ, confidence cao.
- Có guardrail và rollback.

### Giai đoạn 4: Scale out
- Mở rộng sang segment khác nếu drift thấp và quality ổn.

---

## 11) Reasoning loop metrics (Prometheus)

Các metrics mới để theo dõi reasoning loop và rollout:

| Metric | Type | Labels | Ý nghĩa |
|--------|------|--------|---------|
| `supervisor_reasoning_loop_rollout_total` | Counter | `scope`, `outcome` | Quyết định rollout (user/team, enabled/disabled/no_id) |
| `supervisor_reasoning_loop_outcomes_total` | Counter | `status` | Kết quả reasoning loop (`completed`, `needs_clarification`, `needs_review`, `skipped`) |
| `supervisor_reasoning_loop_latency_seconds` | Histogram | — | Latency từng request qua reasoning loop (dùng cho p95/p99) |
| `supervisor_reasoning_loop_fallbacks_total` | Counter | `reason` | Số lần fallback (`rollout_disabled`, `budget_exhausted`, `tool_failed`, `needs_review`) |

### Query gợi ý cho dashboard

```promql
# needs_clarification rate
count(
  rate(supervisor_reasoning_loop_outcomes_total{status="needs_clarification"}[5m])
) /
count(
  rate(supervisor_reasoning_loop_outcomes_total[5m])
)

# needs_review rate
count(
  rate(supervisor_reasoning_loop_outcomes_total{status="needs_review"}[5m])
) /
count(
  rate(supervisor_reasoning_loop_outcomes_total[5m])
)

# Latency p95
histogram_quantile(0.95, rate(supervisor_reasoning_loop_latency_seconds_bucket[5m]))

# Fallback rate (rollout_disabled + budget_exhausted + tool_failed)
count(
  rate(supervisor_reasoning_loop_fallbacks_total[5m])
) /
count(
  rate(supervisor_reasoning_loop_outcomes_total[5m])
)
```

### Rollout gate
- Gate = `feature flag` + `% user` + `% team` (OR logic).
- Bucketing dùng `sha256` deterministic theo `salt` để đảm bảo user cố định.
- Không cần DB migration; chỉ cần điều chỉnh env var và restart service.

---

## 12) Fact Store metrics (Prometheus)

Các metrics để theo dõi structured fact memory:

| Metric | Type | Labels | Ý nghĩa |
|--------|------|--------|---------|
| `supervisor_fact_store_retrievals_total` | Counter | `outcome` | Số lần retrieve facts (`hit`, `miss`, `error`) |
| `supervisor_fact_store_commits_total` | Counter | `outcome` | Số lần extract & store facts (`success`, `error`) |

### Query gợi ý

```promql
# Fact store hit rate
rate(supervisor_fact_store_retrievals_total{outcome="hit"}[5m])
/
rate(supervisor_fact_store_retrievals_total[5m])
```

## 13) Subagent Delegation metrics (Prometheus)

Các metrics để theo dõi parallel subagent execution:

| Metric | Type | Labels | Ý nghĩa |
|--------|------|--------|---------|
| `supervisor_subagent_pool_tasks_total` | Counter | `status` | Số tasks dispatched (`success`, `timeout`, `error`) |
| `supervisor_subagent_pool_latency_seconds` | Histogram | — | Latency từng subagent task |
| `supervisor_subagent_delegation_triggers_total` | Counter | `outcome` | Số lần trigger delegation (`executed`, `skipped`, `no_tasks`) |

### Query gợi ý

```promql
# Subagent success rate
rate(supervisor_subagent_pool_tasks_total{status="success"}[5m])
/
rate(supervisor_subagent_pool_tasks_total[5m])

# Average subagent latency
rate(supervisor_subagent_pool_latency_seconds_sum[5m])
/
rate(supervisor_subagent_pool_latency_seconds_count[5m])
```

## 14) Kết luận ngắn

Insight quan trọng nhất để chạy product theo dõi đa chiều là:

- không học từ một loại feedback duy nhất,
- không gộp mọi ngữ cảnh vào một profile,
- không để learning worker tự quyết ngầm,
- và luôn quan sát được hệ thống đang tin cái gì, vì sao tin, và khi nào phải dừng.

Nói ngắn gọn: hệ thống tốt không phải hệ thống biết nhiều nhất, mà là hệ thống biết điều gì đáng tin và điều gì cần kiểm soát.
