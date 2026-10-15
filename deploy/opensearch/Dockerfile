# OpenSearch dla Scriptury: polski Stempel + ICU (folding, transliteracje); k-NN jest w obrazie.
#   cp -r deploy/opensearch /srv/opensearch && cd /srv/opensearch
#   install -d -o 1000 -g 1000 data && docker compose up -d --build
FROM opensearchproject/opensearch:3.7.0
RUN /usr/share/opensearch/bin/opensearch-plugin install --batch analysis-stempel \
 && /usr/share/opensearch/bin/opensearch-plugin install --batch analysis-icu
