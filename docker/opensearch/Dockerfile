# OpenSearch z pluginami: Stempel (polski stemmer) i ICU (folding greki/hebrajskiego)
FROM opensearchproject/opensearch:2.19.1
RUN bin/opensearch-plugin install --batch analysis-stempel \
 && bin/opensearch-plugin install --batch analysis-icu
