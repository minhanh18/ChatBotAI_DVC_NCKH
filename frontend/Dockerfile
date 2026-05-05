FROM node:20-slim AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps \
  && ROLLUP_VERSION=$(node -p "require('./node_modules/rollup/package.json').version") \
  && npm install --no-save --legacy-peer-deps @rollup/rollup-linux-x64-gnu@$ROLLUP_VERSION

COPY . .
RUN npm run build

FROM nginx:alpine
RUN apk add --no-cache gettext

COPY index.html /usr/share/nginx/html/index.html
COPY assets /usr/share/nginx/html/static/assets
COPY chatbot-inline.js /usr/share/nginx/html/static/chatbot-inline.js
COPY --from=builder /app/dist/assets /usr/share/nginx/html/app/assets
COPY --from=builder /app/dist/app.html /usr/share/nginx/html/app/index.html
COPY nginx.frontend.conf.template /etc/nginx/templates/default.conf.template

ENV BACKEND_HOST=backend
ENV BACKEND_PORT=8000

EXPOSE 80

# Chờ backend DNS sẵn sàng trước khi start nginx
# Render: fromService trả về internal hostname (vd: chatbot-backend-xxxx)
# nginx sẽ crash nếu resolve DNS thất bại ngay lúc khởi động
CMD ["/bin/sh", "-c", \
  "envsubst '$$BACKEND_HOST $$BACKEND_PORT' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && \
  nginx -g 'daemon off;'"]