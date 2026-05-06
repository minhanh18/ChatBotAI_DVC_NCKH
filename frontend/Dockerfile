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

# BACKEND_HOST = full domain của backend (vd: chatbot-backend-qhsy.onrender.com)
# Được inject từ render.yaml qua fromService property: host
ENV BACKEND_HOST=chatbot-backend-qhsy.onrender.com

EXPOSE 80

CMD ["/bin/sh", "-c", \
  "BACKEND_URL=https://${BACKEND_HOST} && \
   echo \"==> Backend URL: ${BACKEND_URL}\" && \
   sed \
     -e \"s|BACKEND_URL_PLACEHOLDER|${BACKEND_URL}|g\" \
     -e \"s|BACKEND_HOST_PLACEHOLDER|${BACKEND_HOST}|g\" \
     /etc/nginx/nginx.template > /etc/nginx/conf.d/default.conf && \
   cat /etc/nginx/conf.d/default.conf | grep backend && \
   nginx -g 'daemon off;'"]
