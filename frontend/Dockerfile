FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

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
CMD ["/bin/sh", "-c", "envsubst '$$BACKEND_HOST $$BACKEND_PORT' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
