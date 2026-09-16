# Agnes API 契约文档

> 基于 2026-08-03 实测数据固化。Agnes API 变更时，先更新此文档 + 响应快照，再跑契约测试确认。
> Base URL: `https://apihub.agnes-ai.cn/v1`（国内域名，直连可达）
> 认证: `Authorization: Bearer <API_KEY>`

---

## 1. 文本对话 — `agnes-2.0-flash`

### 请求
```
POST /chat/completions
Content-Type: application/json
Authorization: Bearer <key>

{
  "model": "agnes-2.0-flash",
  "messages": [{"role": "user", "content": "..."}],
  "stream": false
}
```

### 响应（200）
```json
{
  "choices": [{"message": {"role": "assistant", "content": "..."}}],
  "model": "agnes-2.0-flash"
}
```

---

## 2. 文生图 — `agnes-image-2.1-flash`

### 请求
```
POST /images/generations
Content-Type: application/json
Authorization: Bearer <key>

{
  "model": "agnes-image-2.1-flash",
  "prompt": "雨夜咖啡馆",
  "n": 1,
  "size": "2K",
  "ratio": "16:9"
}
```

> ⚠️ **踩坑#1**：纯文生图**绝不传** `extra_body` / `response_format`，否则报错。

### 响应（200）
```json
{
  "created": 1785731599,
  "data": [{"url": "https://platform-outputs.agnes-ai.space/images/t2i/xxx.png"}],
  "model": "agnes-image-2.1-flash"
}
```

**URL 解析**：`data[*].url`（根级 data 数组）

---

## 3. 图生图 — `agnes-image-2.0-flash`

### 请求
```
POST /images/generations
Content-Type: application/json
Authorization: Bearer <key>

{
  "model": "agnes-image-2.0-flash",
  "prompt": "front view, character design sheet",
  "n": 1,
  "extra_body": {
    "tags": ["img2img"],
    "image": ["<参考图URL或dataURI>"],
    "response_format": "url"
  },
  "size": "2K"
}
```

> ⚠️ **踩坑#6**：Agnes 拒绝 localhost/私有网络 URL（报 "port 8000 is not allowed"）。本地图片须转 base64 data URI（`media_url_to_data_uri`）。

### 响应（200）
```json
{
  "created": 1785732014,
  "data": [{"url": "https://platform-outputs.agnes-ai.space/images/i2i/xxx.png"}],
  "model": "agnes-image-2.0-flash"
}
```

**URL 解析**：`data[*].url`（同文生图）

---

## 4. 图生视频 — `agnes-video-v2.0`（异步轮询）

### 4.1 提交任务

```
POST /videos
Content-Type: application/json
Authorization: Bearer <key>

{
  "model": "agnes-video-v2.0",
  "prompt": "雨夜街道",
  "image": "<首帧URL或dataURI>",
  "image_tail": "<尾帧URL，可选>",
  "width": 1280,
  "height": 720,
  "num_frames": 41,
  "frame_rate": 24
}
```

> ⚠️ `num_frames` 必须 `8n+1` 且 `≤441`。
> ⚠️ 首帧同样需要 Base64 转换（踩坑#6）。

### 提交响应（200）
```json
{
  "id": "task_xxx",
  "video_id": "task_xxx",
  "task_id": "task_xxx",
  "object": "video",
  "model": "agnes-video-v2.0",
  "status": "queued",
  "progress": 0,
  "created_at": 1785732246
}
```

**task_id 解析**：`$.task_id`（根级）

### 4.2 轮询任务

```
GET /videos/{task_id}
Authorization: Bearer <key>
```

### 轮询响应 — 运行中（200）
```json
{
  "status": "in_progress",
  "progress": 30,
  ...
}
```

状态映射：`queued/in_progress/processing/running/pending` → running

### 轮询响应 — 成功（200）
```json
{
  "id": "task_xxx",
  "task_id": "task_xxx",
  "status": "completed",
  "progress": 100,
  "seconds": "1.7",
  "size": "1280x704",
  "metadata": {
    "size_mapping": {
      "adjusted": true,
      "height": 704,
      "ratio": "16:9",
      "width": 1280,
      "resolution": "720p"
    },
    "url": "https://platform-outputs.agnes-ai.space/videos/agnes-video-v2.0/task_xxx.mp4"
  }
}
```

> ⚠️ **踩坑#2/3**：视频 URL 在 `metadata.url`，**不是**根级 `video_url`！
>
> result_jsonpath 须为：`$.metadata.url||$.video_url||$.remixed_from_video_id`

状态映射：`completed/succeeded/success` → succeeded

### 轮询响应 — 失败（200）
```json
{
  "status": "failed",
  "error": "content policy violation",
  "code": "content_filter"
}
```

状态映射：`failed/error` → failed

---

## 5. 状态码映射表

| Agnes status | ProviderStatus |
|-------------|----------------|
| queued / pending | running |
| in_progress / processing / running | running |
| completed / succeeded / success | succeeded |
| failed / error | failed |

---

## 6. 已知限制

| # | 限制 | 影响 | 应对 |
|---|------|------|------|
| 1 | 纯文生图禁 extra_body | 文生图请求体须精简 | `textToImage` 不传 extra_body |
| 2 | 视频结果在 metadata.url | result_jsonpath 须含 metadata.url | DB http_poll_config 已配置 |
| 3 | num_frames 须 8n+1≤441 | 视频帧数受限 | 前端校验 + 默认 121 |
| 4 | 视频自带 AAC 音频 | TTS 可后置 | 导出时 tpad 对齐 |
| 5 | 拒绝 localhost URL | img2img/视频首帧须转 Base64 | `media_url_to_data_uri` |
| 6 | estimatedSeconds 须 int | TaskHandle 字段类型约束 | `int()` 转换 |
| 7 | 响应时间不稳定 | img2img 50s-2min，视频 2-5min | 超时设 1800s |

---

## 7. 响应快照

真实响应快照存放于 `backend/tests/fixtures/agnes_responses/`：
- `text_to_image.json` — 文生图响应
- `image_to_image.json` — 图生图响应
- `video_submit.json` — 视频提交响应
- `video_query_pending.json` — 视频轮询中
- `video_query_completed.json` — 视频完成
- `video_query_failed.json` — 视频失败

契约测试加载这些快照断言。Agnes API 变更时：
1. 更新快照文件
2. 更新本文档
3. 运行 `pytest tests/providers/` 确认适配器与新契约一致
