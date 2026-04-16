FROM node:20-alpine AS build

WORKDIR /app/apps/web

COPY apps/web/package*.json ./
RUN npm ci

COPY apps/web ./

ARG VITE_API_BASE=
ARG VITE_AMAP_WEB_KEY=
ARG VITE_AMAP_SECURITY_JS_CODE=
ARG VITE_AMAP_URI_SRC=arcadegent_web

ENV VITE_API_BASE=${VITE_API_BASE} \
    VITE_AMAP_WEB_KEY=${VITE_AMAP_WEB_KEY} \
    VITE_AMAP_SECURITY_JS_CODE=${VITE_AMAP_SECURITY_JS_CODE} \
    VITE_AMAP_URI_SRC=${VITE_AMAP_URI_SRC}

RUN npm run build

FROM nginx:1.27-alpine

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
