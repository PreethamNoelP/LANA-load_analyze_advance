# Multi-stage build: compile the Vite SPA, then serve the static output with
# nginx, which also reverse-proxies /api/* to the backend container — the
# same rewrite the Vite dev server's proxy does (see vite.config.js), just
# performed by nginx.conf instead of Vite in production.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Baked into the JS bundle at build time (Vite only exposes import.meta.env.VITE_*
# variables that existed when `vite build` ran) — see src/api.js. Leave unset
# for the default, no-auth setup.
ARG VITE_LANA_AUTH_TOKEN=""
ENV VITE_LANA_AUTH_TOKEN=$VITE_LANA_AUTH_TOKEN
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
