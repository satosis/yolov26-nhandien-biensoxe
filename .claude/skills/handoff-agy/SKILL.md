---
name: handoff-agy
description: Claude lập plan, Antigravity (agy CLI) viết code, Claude review. Dùng khi việc sửa/thêm code dài (nhiều file, nhiều dòng) mà phần khó là hiểu project, không phải gõ code; hoặc khi người dùng nói "giao cho antigravity/agy", "để agy code", "tiết kiệm token". Không dùng cho sửa 1–2 dòng (tự sửa rẻ hơn).
---

# Claude plan → Antigravity code → Claude review

Mục tiêu: Claude tốn token vào việc đọc hiểu và kiểm tra; phần viết code chạy trên quota Antigravity.

## 1. Lập plan (Claude)

Đọc đủ code để plan không phải đoán. Ghi `.ai/plans/<ten-ngan>.md` theo mẫu:

```markdown
# <Mục tiêu một câu>

## Bối cảnh
- <file:dòng> — <vì sao liên quan, hàm/biến nào>

## Thay đổi
1. `<đường/dẫn/file>`: <làm gì, ở hàm nào; đoạn code mẫu nếu logic dễ sai>
2. ...

## Không được đụng
- <file/hành vi phải giữ nguyên>

## Kiểm tra
- `<lệnh test cụ thể>` phải pass
- <hành vi cần thấy>
```

Plan phải cụ thể đến mức người chưa đọc project vẫn làm đúng: tên file, tên hàm, quy ước sẵn có. Không dán cả file vào plan; chỉ trỏ đường dẫn và dòng.

## 2. Giao cho Antigravity

```bash
python scripts/agents/agy_exec.py .ai/plans/<ten-ngan>.md
```

- Mặc định model `gemini-3.8-flash-high`. Việc khó: `--model claude-opus-4-6-thinking` hoặc `claude-sonnet-4-6` (vẫn tính quota Antigravity).
- Luôn thêm `--full` (người dùng đã cấp quyền; không có `--full` agy bị chặn lệnh đầu tiên và dừng mà không sửa gì). Gọi lệnh không kèm `cd ... &&` phía trước để khớp quy tắc cho phép.
- Script in STATUS, CONVERSATION id, file thay đổi, và báo cáo FILES/CHECKS/ISSUES. Log đầy đủ ở `.ai/runs/`.

## 3. Review (Claude)

1. `git diff -- <các file trong GIT CHANGES>`: đọc diff, không đọc lại cả file.
2. Tự chạy các lệnh trong mục "Kiểm tra" của plan.
3. Có lỗi: gửi lại đúng cuộc trò chuyện đó, chỉ nêu điểm cần sửa:
   ```bash
   python scripts/agents/agy_exec.py .ai/plans/<ten-ngan>.md --resume <CONVERSATION> --note "<file:dòng — sai gì, sửa thế nào>"
   ```
   Sau 2 vòng vẫn sai thì Claude tự sửa phần còn lại.
4. Báo người dùng chỉ nội dung thay đổi: hệ thống giờ khác trước thế nào, 1–5 dòng ngắn. Không kể ai làm (agy hay Claude), tên file, kết quả test, nguyên nhân kỹ thuật. Chỉ thêm một dòng khi còn việc chưa xong, hoặc người dùng phải làm gì đó thì thay đổi mới có tác dụng.

Không commit thay người dùng trừ khi được yêu cầu.
