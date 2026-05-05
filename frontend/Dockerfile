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

# envsubst điền BACKEND_HOST/PORT vào nginx config rồi start nginx
# Không cần chờ DNS — nginx resolve per-request nhờ resolver trong template
CMD ["/bin/sh", "-c", \
  "envsubst '$$BACKEND_HOST $$BACKEND_PORT' \
   < /etc/nginx/templates/default.conf.template \
   > /etc/nginx/conf.d/default.conf \
   && nginx -g 'daemon off;'"]
