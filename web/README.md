# 公众门户（web/ + api/）

面向用户的展示门户；浏览器通过 Next.js 同源 `/api/*` 访问后端，外网访问不会出现 `Failed to fetch`。

## 架构

```text
浏览器 → Nginx(:80) → Next.js(:3001) → /api/* 反代 → FastAPI(:8000) → MySQL
app.py Streamlit 管理后台（8501，可停用）
```

## 快速启动（开发）

```bash
./scripts/run_portal.sh
```

- 门户：http://localhost:3001（3000 被占用时自动改用 3001）
- API 文档：http://127.0.0.1:8000/docs

## 生产 / 长期运行

```bash
./scripts/run_portal_prod.sh          # build + next start
# 或 systemd（见 deploy/ai-safety-portal-*.service）
```

## 通过 IP/域名访问（Nginx）

```bash
# 停用旧 Streamlit 站点
sudo systemctl stop ai-safety
sudo systemctl disable ai-safety

sudo cp deploy/nginx-ai-safety-portal.conf /etc/nginx/sites-available/ai-safety-portal
sudo ln -sf /etc/nginx/sites-available/ai-safety-portal /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/ai-safety
sudo nginx -t && sudo systemctl reload nginx
```

浏览器访问：`http://服务器IP/`

## 环境变量（web/.env.local）

```env
API_INTERNAL_URL=http://127.0.0.1:8000
```

浏览器端**不要**把 `NEXT_PUBLIC_API_URL` 设为 `127.0.0.1`（外网会失败）；已改为同源反代。

## 主要页面

| 路径 | 说明 |
|------|------|
| `/` | 首页 |
| `/policy` | 政策监管 |
| `/meetings` | 国际会议 |
| `/literature` | 文献情报 |
| `/about` | 关于 |
