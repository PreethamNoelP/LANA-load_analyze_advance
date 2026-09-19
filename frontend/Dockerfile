# Multi-stage build: compile the Vite SPA, then serve the static output with
# nginx, which also reverse-proxies /api/* to the backend container — the
# same rewrite the Vite dev server's proxy does (see vite.config.js), just
# performed by nginx.conf instead of Vite in production.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# No auth token is baked in. It used to be, via VITE_LANA_AUTH_TOKEN, which
# meant the secret shipped inside the JavaScript where any visitor could read
# it and rotating it required rebuilding this image. The browser now exchanges
# the token for an HttpOnly cookie at runtime (POST /auth/session), so this
# build is identical whether or not auth is enabled.
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
