#!/bin/sh
# Runs once, after Nextcloud installs, before any user logs in. Without it the
# test user gets Nextcloud's sample files (PDFs, photos), which every sync would
# index for nothing.
set -eu
php /var/www/html/occ config:system:set skeletondirectory --value=''
php /var/www/html/occ config:system:set templatedirectory --value=''
