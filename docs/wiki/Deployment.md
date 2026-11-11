# Deployment

## 环境要求

- Docker Engine 24+
- Docker Compose v2
- 建议 2 核 CPU、1 GB 内存、2 GB 可用磁盘
- 默认使用宿主机 8000 端口

## Docker Compose（推荐）

\`\`\`bash
git clone https://github.com/cvpia/telegram-media-downloader-web.git
cd telegram-media-downloader-web
docker compose up -d --build
docker compose ps
\`\`\`

访问：

- 官网：\`http://服务器IP:8000/\`
- 管理后台：\`http://服务器IP:8000/dashboard\`

## 预构建镜像

\`\`\`bash
docker pull xxoo520zls/telegram-media-downloader-web:3.14.0
docker compose up -d
\`\`\`

## Python 原生运行

需要 Python 3.12 或 3.13。

\`\`\`bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
\`\`\`

Windows PowerShell 激活虚拟环境：

\`\`\`powershell
.\.venv\Scripts\Activate.ps1
\`\`\`

## 反向代理

公网部署应在 Nginx、Caddy 或云负载均衡层启用 HTTPS。SSE 下载状态推送需要关闭响应缓冲并延长读取超时。

\`\`\`nginx
server {
    listen 443 ssl http2;
    server_name media.example.com;

    client_max_body_size 1024m;
    proxy_read_timeout 3600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
\`\`\`
