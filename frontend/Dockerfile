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
COPY nginx.frontend.conf.template /etc/nginx/nginx.template

ENV BACKEND_HOST=backend
ENV BACKEND_PORT=8000

EXPOSE 80

CMD ["/bin/sh", "-c", \
  "RESOLVER=$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf) && \
   echo \"Using DNS resolver: $RESOLVER\" && \
   sed \
     -e \"s|RESOLVER_PLACEHOLDER|${RESOLVER}|g\" \
     -e \"s|BACKEND_HOST_PLACEHOLDER|${BACKEND_HOST}|g\" \
     -e \"s|BACKEND_PORT_PLACEHOLDER|${BACKEND_PORT}|g\" \
     /etc/nginx/nginx.template > /etc/nginx/conf.d/default.conf && \
   echo 'Nginx config written. Starting...' && \
   nginx -g 'daemon off;'"]
