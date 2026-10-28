# BAEC HTTPS proxy image (V0.4 Phase 5; base refreshed in Phase 6). The official nginx image, pinned by digest, with BAEC's boundary template
# and without the image's default site (which would also listen on port 80 and serve its own page).
# TLS certificate and key are mounted at run time (/etc/nginx/tls/fullchain.pem, privkey.pem); none is baked in.
# Phase 6: the 1.28 branch image is no longer rebuilt (its tag still named the Phase 5 digest), so the base moved to
# the current stable branch, 1.30, whose image carries the Alpine security fixes. Same template, same directives.
FROM nginx:1.30-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951

RUN rm -f /etc/nginx/conf.d/default.conf
COPY templates/ /etc/nginx/templates/
ENV NGINX_ENVSUBST_FILTER="^BAEC_"
